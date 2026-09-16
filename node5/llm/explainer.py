"""Optional LLM explanation layer (architecture §5.15–§5.17, D-LLM).

The LLM explains already-computed Node 4 decisions; it never decides anything.
Every response is structurally constrained (`LLMExplanation`) and passed through
the deterministic `explanation_validator`. Any failure falls back to templates.
"""

from __future__ import annotations

import json
from typing import Any

from config.models import Node5Config
from node5.llm.schemas import LLMExplanation
from node5.report.explanation_validator import build_allowed_facts, validate_explanation
from router.llm_mapper import LlmClient
from schemas.node4 import RankedAccount

_SYSTEM_RULES = """You are generating client-facing explanatory text for a churn-risk report.

You must only describe information contained in the supplied structured input.

You must not:
- change risk levels, scores, rankings, or confidence
- invent evidence, customer facts, percentages, dates, or causes
- add new risk factors
- introduce recommendations
- contradict the structured input
- state or imply a different risk level
- describe confidence as a probability of churn

Return ONLY a single JSON object with exactly these keys:
{"headline": <string>, "summary": <2-4 sentences>, "reason_explanations": [<string>, ...]}

Do not return any other keys (for example risk_level, rank, score, confidence,
recommendation, or evidence). If information is unavailable, do not speculate.
"""


def build_prompt(account: RankedAccount, display_name: str) -> str:
    """Build the explainer prompt from the validated structured facts only (§5.15)."""
    payload: dict[str, Any] = {
        "customer_id": account.customer_id,
        "display_name": display_name,
        "risk_level": account.combined_risk_level.value,
        "rank": account.rank,
        "combined_score": account.combined_score,
        "combined_confidence": account.combined_confidence,
        "primary_reasons": [
            {"reason_type": reason.reason_type.value, "source": reason.source}
            for reason in account.primary_reasons
        ],
        "quantitative": {
            "risk_score": account.quantitative.risk_score,
            "survival_prob_90d": account.quantitative.survival_prob_90d,
            "top_drivers": list(account.quantitative.top_drivers),
            "customer_state": account.quantitative.customer_state.value,
        },
        "qualitative": {
            "support_data_status": account.qualitative.support_data_status.value,
            "signal_strength": account.qualitative.signal_strength.value,
            "churn_language_detected": account.qualitative.churn_language_detected,
            "escalation_signal": account.qualitative.escalation_signal,
            "top_flags": [
                entry.get("flag_type")
                for entry in account.qualitative.top_flags
                if isinstance(entry, dict)
            ],
        },
    }
    return f"{_SYSTEM_RULES}\nSTRUCTURED INPUT:\n{json.dumps(payload, indent=2, sort_keys=True)}"


def _extract_json(text: str) -> str:
    """Extract the first JSON object from possibly fenced model output."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("LLM response did not contain a JSON object")
    return stripped[start : end + 1]


def explain_account(
    account: RankedAccount,
    display_name: str,
    config: Node5Config,
    client: LlmClient,
    counters: dict[str, int],
    warnings: list[str],
    *,
    recommended_action: str | None = None,
) -> tuple[str | None, str | None]:
    """Return validated ``(headline, summary)`` or ``(None, None)`` on failure."""
    allowed = build_allowed_facts(account, recommended_action, display_name)
    prompt = build_prompt(account, display_name)
    last_error: str | None = None
    for _ in range(config.llm_max_retries + 1):
        counters["llm_calls"] += 1
        try:
            raw = client.complete(prompt, temperature=config.llm_temperature)
            explanation = LLMExplanation.model_validate_json(_extract_json(raw))
            violations = validate_explanation(
                explanation.headline,
                explanation.summary,
                explanation.reason_explanations,
                allowed,
            )
            if violations:
                raise ValueError("unsupported explanation: " + "; ".join(violations))
            return explanation.headline, explanation.summary
        except Exception as exc:  # noqa: BLE001 - any failure falls back safely
            counters["llm_failures"] += 1
            last_error = str(exc)
    warnings.append(
        "LLM explanation rejected after "
        f"{config.llm_max_retries + 1} attempt(s); deterministic template used "
        f"({last_error})."
    )
    return None, None

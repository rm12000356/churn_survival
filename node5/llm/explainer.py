"""Optional LLM explanation layer (architecture §5.15–§5.17, D-LLM).

The LLM explains already-computed Node 4 decisions; it never decides anything.
Every response is structurally constrained (`LLMExplanation`) and passed through
the deterministic `explanation_validator`. Any failure falls back to templates.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import ValidationError

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


#: HTTP statuses after which retrying any account is pointless (bad/expired key).
_AUTH_STATUSES = {401, 403}


def _provider_status(exc: BaseException) -> int | None:
    """HTTP status of a provider error, when the exception carries a response."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _rejection_code(exc: BaseException) -> str:
    """A short reason code; never the model output or the provider's message.

    Validation messages and pydantic errors can quote the LLM's text, which must
    not reach the persisted report or the API (REVIEW N-M5).
    """
    if isinstance(exc, UnsupportedExplanationError):
        return f"unsupported_claims({exc.n_violations})"
    if isinstance(exc, ValidationError):
        return "schema_invalid"
    if isinstance(exc, ValueError):
        return "invalid_json"
    status = _provider_status(exc)
    return f"provider_error({status})" if status else f"provider_error({type(exc).__name__})"


class UnsupportedExplanationError(ValueError):
    """The explanation made claims the deterministic validator rejected."""

    def __init__(self, violations: list[str]) -> None:
        super().__init__("unsupported explanation: " + "; ".join(violations))
        self.n_violations = len(violations)


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
    """Return validated ``(headline, summary)`` or ``(None, None)`` on failure.

    ``counters`` gains ``llm_provider_errors`` (the call itself failed) and
    ``llm_auth_errors`` (401/403), kept apart from validation rejections so an
    outage is not reported as "the model wrote unsupported claims" (REVIEW N-M4).
    An auth error stops the retries at once.
    """
    allowed = build_allowed_facts(account, recommended_action, display_name)
    prompt = build_prompt(account, display_name)
    last_error: str | None = None
    for _ in range(config.llm_max_retries + 1):
        counters["llm_calls"] += 1
        try:
            raw = client.complete(prompt, temperature=config.llm_temperature)
        except Exception as exc:  # noqa: BLE001 - provider failure falls back safely
            counters["llm_failures"] += 1
            counters["llm_provider_errors"] = counters.get("llm_provider_errors", 0) + 1
            last_error = _rejection_code(exc)
            if _provider_status(exc) in _AUTH_STATUSES:
                counters["llm_auth_errors"] = counters.get("llm_auth_errors", 0) + 1
                break
            continue
        try:
            explanation = LLMExplanation.model_validate_json(_extract_json(raw))
            violations = validate_explanation(
                explanation.headline,
                explanation.summary,
                explanation.reason_explanations,
                allowed,
            )
            if violations:
                raise UnsupportedExplanationError(violations)
            return explanation.headline, explanation.summary
        except Exception as exc:  # noqa: BLE001 - any failure falls back safely
            counters["llm_failures"] += 1
            last_error = _rejection_code(exc)
    warnings.append(
        "LLM explanation rejected after "
        f"{config.llm_max_retries + 1} attempt(s); deterministic template used "
        f"({last_error})."
    )
    return None, None

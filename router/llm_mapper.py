"""LLM-assisted mapping-report path (architecture §1.6, ROADMAP Task 2.5).

The LLM produces a reviewable *MappingReport* — never a direct transformation.
A human confirms it, and ``confirm_and_persist`` stores it as a deterministic
MappingConfig in ``config/mappings/``. Future files whose fingerprint matches
the confirmed report are handled by ``MappingConfigAdapter`` without any LLM.

The LLM has zero authority here: its only output is a proposed mapping that is
Pydantic-validated and human-confirmed before it becomes configuration.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from config.models import MappingConfig
from schemas.mapping import MappingReport, SourceFingerprint

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class MappingReportError(RuntimeError):
    """Raised when the LLM mapping path cannot produce a valid, confirmable report."""


@dataclass(frozen=True)
class LlmClient:
    """Provider-agnostic chat client (thin httpx wrapper; temperature capped at 0.2)."""

    provider: str
    model: str
    api_key: str
    base_url: str | None = None

    def complete(self, prompt: str) -> str:
        import httpx

        if self.provider == "openai":
            url = f"{self.base_url or 'https://api.openai.com/v1'}/chat/completions"
            payload = {
                "model": self.model,
                "temperature": 0.2,
                "messages": [{"role": "user", "content": prompt}],
            }
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }
            result_key: tuple[Any, ...] = ("choices", 0, "message", "content")
        elif self.provider == "anthropic":
            url = f"{self.base_url or 'https://api.anthropic.com'}/v1/messages"
            payload = {
                "model": self.model,
                "max_tokens": 4096,
                "temperature": 0.2,
                "messages": [{"role": "user", "content": prompt}],
            }
            headers = {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            }
            result_key = ("content", 0, "text")
        else:
            raise MappingReportError(f"unsupported LLM_PROVIDER {self.provider!r}")

        response = httpx.post(url, json=payload, headers=headers, timeout=60.0)
        response.raise_for_status()
        data: Any = response.json()
        for part in result_key:
            data = data[part]
        return str(data)


def create_llm_client() -> LlmClient:
    """Build a client from settings; fail loudly when the LLM is not configured."""
    from config.settings import get_settings

    settings = get_settings()
    if settings.LLM_PROVIDER == "none":
        raise MappingReportError(
            "LLM_PROVIDER=none; cannot generate a mapping report. Configure .env or "
            "register a deterministic adapter."
        )
    if not settings.LLM_API_KEY or not settings.LLM_MODEL:
        raise MappingReportError(
            "LLM_API_KEY and LLM_MODEL are required when LLM_PROVIDER != 'none'"
        )
    return LlmClient(
        provider=settings.LLM_PROVIDER,
        model=settings.LLM_MODEL,
        api_key=settings.LLM_API_KEY,
    )


def build_mapping_prompt(fingerprint: SourceFingerprint, sample: Any, *, n_rows: int = 25) -> str:
    """Prompt the LLM with headers + a small row sample + sheet names (§1.6)."""
    import pandas as pd

    lines = [
        "You are a data-format translation assistant. Produce a JSON MappingReport that maps this "
        "data to the canonical churn-survival schema. Output ONLY the JSON object, no prose.",
        "",
        "Fingerprint:",
        json.dumps(fingerprint.model_dump(mode="json"), indent=2),
        "",
        "Sample rows:",
    ]
    frames: list[tuple[str | None, pd.DataFrame]] = []
    if isinstance(sample, dict):
        for sheet, frame in sample.items():
            frames.append((str(sheet), frame))
    elif isinstance(sample, pd.DataFrame):
        frames.append((None, sample))
    else:
        raise TypeError(f"cannot build a prompt from {type(sample).__name__}")

    for sheet, frame in frames:
        if sheet is not None:
            lines.append(f"--- sheet: {sheet} ---")
        lines.append(frame.head(n_rows).to_csv(index=False))

    lines.append(
        "Return JSON with keys: source_fingerprint, proposed_mappings, unmapped_columns, "
        "suggested_extra_features, data_quality_flags, "
        "recommended_action, llm_model_used, generated_at."
    )
    return "\n".join(lines)


def extract_json(text: str) -> str:
    """Pull the JSON object out of an LLM reply (strip optional code fences)."""
    match = _JSON_BLOCK.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def generate_mapping_report(
    fingerprint: SourceFingerprint,
    sample: Any,
    *,
    client: LlmClient | None = None,
    n_rows: int = 25,
) -> MappingReport:
    """Generate a Pydantic-validated MappingReport via the LLM client."""
    client = client or create_llm_client()
    prompt = build_mapping_prompt(fingerprint, sample, n_rows=n_rows)
    raw = client.complete(prompt)
    try:
        payload = json.loads(extract_json(raw))
    except json.JSONDecodeError as exc:
        raise MappingReportError(f"LLM returned non-JSON output: {exc}") from exc
    try:
        return MappingReport.model_validate(payload)
    except ValidationError as exc:
        raise MappingReportError(f"LLM output failed mapping-report validation: {exc}") from exc


def confirm_and_persist(
    report: MappingReport,
    *,
    config_dir: Path,
    confirmed_by: str,
    confirmed_at: datetime | None = None,
) -> MappingConfig:
    """Store a human-confirmed report as a deterministic MappingConfig (§1.6)."""
    confirmed_at = confirmed_at or datetime.now(UTC)
    mapping_version = "map_" + confirmed_at.strftime("%Y%m%dT%H%M%SZ")
    config = MappingConfig(
        mapping_version=mapping_version,
        report=report,
        confirmed_at=confirmed_at,
        confirmed_by=confirmed_by,
    )
    mappings_dir = Path(config_dir) / "mappings"
    mappings_dir.mkdir(parents=True, exist_ok=True)
    path = mappings_dir / f"{mapping_version}.json"
    path.write_text(
        json.dumps(config.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return config

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
import random
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from adapters.mapping_adapter import _IDENTITY_FIELDS, validate_transformation
from config.models import MappingConfig
from schemas.canonical import CoreFeatures
from schemas.mapping import MappingReport, SourceFingerprint

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

_CORE_KEYS = set(CoreFeatures.model_fields.keys())


class MappingReportError(RuntimeError):
    """Raised when the LLM mapping path cannot produce a valid, confirmable report."""


# --- HTTP transport (REVIEW §5) ------------------------------------------------
# One pooled, thread-safe ``httpx.Client`` is shared by every ``LlmClient`` so
# concurrent Node 3 / Node 5 workers reuse keep-alive connections instead of
# paying a TCP+TLS handshake per call. Rate-limit (429) and transient 5xx /
# transport errors are retried with backoff; the retries affect timing only,
# never the content of a successful response.
_HTTP_TIMEOUT_S = 60.0
_HTTP_MAX_CONNECTIONS = 32
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_S = 0.5
_MAX_RETRY_AFTER_S = 30.0

_http_lock = threading.Lock()
_http_client: Any | None = None
_sleep = time.sleep  # patched in tests


def _shared_http_client() -> Any:
    """Return the process-wide pooled ``httpx.Client`` (created lazily)."""
    global _http_client
    import httpx

    with _http_lock:
        if _http_client is None or _http_client.is_closed:
            _http_client = httpx.Client(
                timeout=_HTTP_TIMEOUT_S,
                limits=httpx.Limits(
                    max_connections=_HTTP_MAX_CONNECTIONS,
                    max_keepalive_connections=_HTTP_MAX_CONNECTIONS,
                ),
            )
        return _http_client


def _retry_delay(response: Any | None, attempt: int) -> float:
    """``Retry-After`` seconds when the provider sends one, else exponential backoff."""
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after is not None:
            try:
                return min(max(float(retry_after), 0.0), _MAX_RETRY_AFTER_S)
            except ValueError:
                pass  # HTTP-date form: fall back to backoff
    return _BACKOFF_BASE_S * (2**attempt) + random.uniform(0, 0.25)


def _post_with_retry(url: str, payload: dict[str, Any], headers: dict[str, str]) -> Any:
    """POST via the shared client; retry 429/5xx/transport errors, raise the rest."""
    import httpx

    client = _shared_http_client()
    for attempt in range(_MAX_ATTEMPTS):
        last = attempt == _MAX_ATTEMPTS - 1
        try:
            response = client.post(url, json=payload, headers=headers)
        except httpx.TransportError:
            if last:
                raise
            _sleep(_retry_delay(None, attempt))
            continue
        if response.status_code in _RETRY_STATUSES and not last:
            _sleep(_retry_delay(response, attempt))
            continue
        response.raise_for_status()
        return response
    raise AssertionError("unreachable")  # pragma: no cover


@dataclass(frozen=True)
class LlmClient:
    """Provider-agnostic chat client (thin httpx wrapper).

    ``complete`` accepts an optional keyword-only ``temperature`` (default 0.2);
    Node 3 passes its configured value, callers that omit it keep the historical
    0.2 behaviour.
    """

    provider: str
    model: str
    api_key: str
    base_url: str | None = None

    def complete(self, prompt: str, *, temperature: float = 0.2) -> str:
        if self.provider == "openai":
            url = f"{self.base_url or 'https://api.openai.com/v1'}/chat/completions"
            payload = {
                "model": self.model,
                "temperature": temperature,
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
                "temperature": temperature,
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

        response = _post_with_retry(url, payload, headers)
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


def _rules_block() -> str:
    identity = ", ".join(sorted(_IDENTITY_FIELDS))
    core_keys = ", ".join(sorted(_CORE_KEYS))
    return f"""
STRICT RULES — violating any of them invalidates the report:
1. Output ONLY a single JSON object matching the EXACT schema below. No prose,
   no markdown code fences, no keys not listed.
2. target_field MUST be exactly one of the identity fields ({identity}) or
   core.<KEY> where <KEY> is one of: {core_keys}.
   NEVER invent a core key.
3. transformation MUST be exactly one of these strings (verbatim):
   - "identity"
   - "str.strip()"
   - "to_float"
   - "to_int"
   - "parse_date"
   - "months_before(reference_date)"   (a months-of-tenure column -> observation_start)
   - "snapshot_end(reference_date)"    (no churn date; window collapses to the cut-off)
   - "row_number"                      (ONLY when mapping to "customer_id" and the file
                                        has no usable ID column)
   - 'map({{...}})'                     a Python dict literal, e.g.
                                        map({{'Churned': 1, 'Active': 0}})
   NEVER invent a transformation string.
4. Do NOT map leakage / derived / future-looking columns (churn labels, churn
   scores, credit/risk scores, predictions, risk scores, "days ago" recency,
   forecasts, or any column named around churn/attrition/score/prediction/
   risk/label/future) into a core.<KEY>. They may go in suggested_extra_features
   only, or stay unmapped.
5. Do NOT map an outcome/churn flag to a core.<KEY>; it belongs to
   "event_observed" (1 = churned, 0 = active).
6. source_column MUST be the exact raw column header, verbatim.
7. Storage-only fields go in suggested_extra_features ({{"source": <exact header>,
   "suggested_key": <snake_case>}}) — never in target_field.
8. If a column is ambiguous or harmful, leave it unmapped rather than guessing.
9. Do NOT force-fit a column into a core.<KEY> whose meaning does not match.
   A dataset with no core-aligned features should simply propose NO core
   mappings (an empty or small proposed_mappings list is correct).
"""

def _exm(
    source: str, target: str, confidence: float, transformation: str, notes: str | None
) -> dict:
    return {
        "source_column": source,
        "target_field": target,
        "confidence": confidence,
        "transformation": transformation,
        "notes": notes,
    }


_EXAMPLE_1 = {
    "proposed_mappings": [
        _exm("Cust ID", "customer_id", 0.99, "str.strip()", "account id"),
        _exm("Signup Date", "observation_start", 0.99, "parse_date", "mixed date formats"),
        _exm("Cancellation Date", "observation_end", 0.9, "parse_date", "blank = active"),
        _exm("Account Status", "event_observed", 0.99, "map({'Cancelled': 1, 'Active': 0})", None),
        _exm("Plan", "core.plan_tier", 0.9, "str.strip()", None),
        _exm("Contract Length (Months)", "core.contract_length_months", 0.9, "to_float", None),
        _exm("Avg Weekly Active Days", "core.usage_frequency", 0.8, "to_float", None),
        _exm("Support Tickets (Last 90 Days)", "core.support_tickets_90d", 0.9, "to_float", None),
    ],
    "unmapped_columns": ["Internal Notes"],
    "suggested_extra_features": [
        {"source": "Account Number", "suggested_key": "account_number"},
        {"source": "Tier", "suggested_key": "tier"},
        {"source": "Legacy Churn Score", "suggested_key": "legacy_churn_score"},
        {"source": "Last Login Days Ago", "suggested_key": "last_login_days_ago"},
    ],
    "data_quality_flags": [
        "Mixed date formats normalized via parse_date",
        "Legacy Churn Score kept out of modeling features (leakage risk)",
    ],
    "recommended_action": "create_deterministic_adapter",
    "llm_model_used": "example",
    "generated_at": "2026-08-19T00:00:00Z",
}

_EXAMPLE_2 = {
    "proposed_mappings": [
        _exm("CustomerID", "customer_id", 0.99, "to_int", "numeric account id"),
        _exm(
            "Tenure (Months)", "observation_start", 0.9, "months_before(reference_date)",
            "no signup date; derived from months of tenure",
        ),
        _exm(
            "Tenure (Months)", "observation_end", 0.9, "snapshot_end(reference_date)",
            "snapshot cut-off",
        ),
        _exm(
            "Subscription Status", "event_observed", 0.99,
            "map({'Churned': 1, 'Active': 0})", "1 = churned",
        ),
        _exm("Monthly Fee", "core.monthly_charges", 0.9, "to_float", None),
    ],
    "unmapped_columns": [],
    "suggested_extra_features": [
        {"source": "Region", "suggested_key": "region"},
        {"source": "ARPU", "suggested_key": "arpu"},
    ],
    "data_quality_flags": [],
    "recommended_action": "create_deterministic_adapter",
    "llm_model_used": "example",
    "generated_at": "2026-08-19T00:00:00Z",
}


def build_mapping_prompt(fingerprint: SourceFingerprint, sample: Any, *, n_rows: int = 25) -> str:
    """Prompt the LLM with headers + a small row sample + sheet names (§1.6)."""
    import pandas as pd

    lines = [
        "You are a data-format translation assistant. You translate a messy "
        "customer-data export into a MappingReport PROPOSAL for human review. "
        "Produce ONLY a single JSON object with exactly these keys:",
        "  source_fingerprint, proposed_mappings, unmapped_columns, "
        "suggested_extra_features, data_quality_flags, recommended_action, "
        "llm_model_used, generated_at",
        "",
        "Schema of proposed_mappings items: "
        '{"source_column": str, "target_field": str, "confidence": 0-1, '
        '"transformation": str, "notes": str|null}.',
        "",
        "Schema of suggested_extra_features items: "
        '{"source": str, "suggested_key": str}.',
        _rules_block(),
        "=== EXAMPLE 1 (messy SaaS export with leakage/decoy columns) ===",
        json.dumps(_EXAMPLE_1, indent=2),
        "",
        "=== EXAMPLE 2 (snapshot with tenure months, no churn dates) ===",
        json.dumps(_EXAMPLE_2, indent=2),
        "",
        "Now translate the data below. Copy source_fingerprint verbatim.",
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

    return "\n".join(lines)


def extract_json(text: str) -> str:
    """Pull the JSON object out of an LLM reply (strip optional code fences)."""
    match = _JSON_BLOCK.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def validate_mapping_report(report: MappingReport) -> None:
    """Strict, deterministic validation of a MappingReport before it can be used.

    Guards the LLM path (and the human-confirm path) against:
    - transformation strings outside the audited whitelist (architecture §1.6);
    - ``row_number`` used anywhere other than ``customer_id``;
    - ``core.<key>`` targets whose key is not in the ``CoreFeatures`` union
      (blocks invented core keys and leakage/derived columns smuggled into
      modeling features);
    - target fields that are neither identity fields nor ``core.<approved key>``
      (storage-only fields belong in ``suggested_extra_features``).
    """
    for mapping in report.proposed_mappings:
        try:
            validate_transformation(mapping.transformation)
        except ValueError as exc:
            raise MappingReportError(
                f"invalid mapping for source_column {mapping.source_column!r}: {exc}"
            ) from exc
        target = mapping.target_field
        if mapping.transformation.strip() == "row_number" and target != "customer_id":
            raise MappingReportError(
                "row_number is only valid for target_field 'customer_id', "
                f"not {target!r}"
            )
        if target in _IDENTITY_FIELDS:
            continue
        if target.startswith("core."):
            key = target[len("core."):]
            if key not in _CORE_KEYS:
                raise MappingReportError(
                    f"target_field {target!r} is not an approved core key; core "
                    f"keys must be one of: {', '.join(sorted(_CORE_KEYS))}"
                )
            continue
        raise MappingReportError(
            f"target_field {target!r} is neither an identity field "
            f"({', '.join(sorted(_IDENTITY_FIELDS))}) nor core.<approved key>; "
            "storage-only fields must be listed in suggested_extra_features"
        )

    targets = [mapping.target_field for mapping in report.proposed_mappings]
    duplicates = sorted({target for target in targets if targets.count(target) > 1})
    if duplicates:
        raise MappingReportError(
            f"each target_field may be mapped once; duplicated: {', '.join(duplicates)}"
        )
    missing = sorted(_IDENTITY_FIELDS - set(targets))
    if missing:
        raise MappingReportError(
            "a mapping must map every identity field; missing: " + ", ".join(missing)
        )


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
    if not isinstance(payload, dict):
        raise MappingReportError("LLM output must be a JSON object (a mapping report)")
    payload["llm_model_used"] = client.model
    # The fingerprint routes every future file to this mapping: it is the one
    # we computed, never a copy the model could mistype (one wrong hex digit
    # would bind the mapping to a different shape).
    payload["source_fingerprint"] = fingerprint.model_dump(mode="json")
    try:
        report = MappingReport.model_validate(payload)
    except ValidationError as exc:
        raise MappingReportError(f"LLM output failed mapping-report validation: {exc}") from exc
    validate_mapping_report(report)
    return report


def confirm_and_persist(
    report: MappingReport,
    *,
    config_dir: Path,
    confirmed_by: str,
    confirmed_at: datetime | None = None,
    node1_config_version: str | None = None,
) -> MappingConfig:
    """Store a human-confirmed report as a deterministic MappingConfig (§1.6).

    Refuses a second mapping for a shape that already has one: two confirmed
    configs with the same ``headers_hash`` make routing ambiguous and would stop
    every run from loading adapters. An existing config file is never overwritten.
    """
    validate_mapping_report(report)
    mappings_dir = Path(config_dir) / "mappings"
    existing = find_confirmed_mapping(mappings_dir, report.source_fingerprint.headers_hash)
    if existing is not None:
        raise MappingAlreadyConfirmedError(
            f"a confirmed mapping already exists for this dataset shape: {existing}; "
            "it is used automatically — remove it first to replace it",
            mapping_version=existing,
        )
    confirmed_at = confirmed_at or datetime.now(UTC)
    mapping_version = "map_" + confirmed_at.strftime("%Y%m%dT%H%M%SZ")
    config = MappingConfig(
        mapping_version=mapping_version,
        report=report,
        confirmed_at=confirmed_at,
        confirmed_by=confirmed_by,
        node1_config_version=node1_config_version,
    )
    mappings_dir.mkdir(parents=True, exist_ok=True)
    path = mappings_dir / f"{mapping_version}.json"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(
                json.dumps(config.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
            )
    except FileExistsError as exc:
        raise MappingReportError(
            f"mapping config {path.name} already exists; retry in a second"
        ) from exc
    return config


class MappingAlreadyConfirmedError(ValueError):
    """A confirmed mapping already exists for the report's ``headers_hash``."""

    def __init__(self, message: str, *, mapping_version: str) -> None:
        super().__init__(message)
        self.mapping_version = mapping_version


def find_confirmed_mapping(mappings_dir: Path, headers_hash: str) -> str | None:
    """Return the ``mapping_version`` confirmed for ``headers_hash``, if any."""
    if not mappings_dir.is_dir():
        return None
    for path in sorted(mappings_dir.glob("map_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            stored_hash = payload["report"]["source_fingerprint"]["headers_hash"]
        except (OSError, ValueError, KeyError, TypeError):
            continue  # unreadable configs are reported by the adapter loader
        if stored_hash == headers_hash:
            return str(payload.get("mapping_version") or path.stem)
    return None

from __future__ import annotations

import atexit
import contextlib
import json
import math
import os
import random
import re
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args

from pydantic import ValidationError

from adapters.mapping_adapter import (
    _IDENTITY_FIELDS,
    transformation_output_kind,
    validate_transformation,
)
from config.models import RESERVED_FEATURE_KEYS, DeclaredFeature, MappingConfig, Node1Config
from schemas.canonical import CoreFeatures
from schemas.mapping import (
    FEATURE_KEY_PATTERN,
    ApprovedFeature,
    MappingReport,
    ProposedMapping,
    SourceFingerprint,
    SuggestedExtraFeature,
)

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)

_CORE_KEYS = set(CoreFeatures.model_fields.keys())
_FEATURE_PREFIX = "feature."


class MappingReportError(RuntimeError):
    ...


_HTTP_TIMEOUT_S = 60.0
_HTTP_MAX_CONNECTIONS = 32
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_S = 0.5
_MAX_RETRY_AFTER_S = 30.0

_http_lock = threading.Lock()
_http_client: Any | None = None
_http_client_pid: int | None = None
_sleep = time.sleep


def _close_shared_http_client() -> None:
    client = _http_client
    if client is not None and _http_client_pid == os.getpid():
        with contextlib.suppress(Exception):
            client.close()


atexit.register(_close_shared_http_client)


def _shared_http_client() -> Any:
    global _http_client, _http_client_pid
    import httpx

    with _http_lock:
        if _http_client is not None and _http_client_pid != os.getpid():
            _http_client = None
        if _http_client is None or _http_client.is_closed:
            _http_client = httpx.Client(
                timeout=_HTTP_TIMEOUT_S,
                limits=httpx.Limits(
                    max_connections=_HTTP_MAX_CONNECTIONS,
                    max_keepalive_connections=_HTTP_MAX_CONNECTIONS,
                ),
            )
            _http_client_pid = os.getpid()
        return _http_client


def _retry_delay(response: Any | None, attempt: int) -> float:
    if response is not None:
        retry_after = response.headers.get("retry-after")
        if retry_after is not None:
            try:
                seconds = float(retry_after)
            except ValueError:
                seconds = math.nan
            if math.isfinite(seconds):
                return min(max(seconds, 0.0), _MAX_RETRY_AFTER_S)
    return _BACKOFF_BASE_S * (2**attempt) + random.uniform(0, 0.25)


def _post_with_retry(url: str, payload: dict[str, Any], headers: dict[str, str]) -> Any:
    import httpx

    client = _shared_http_client()
    for attempt in range(_MAX_ATTEMPTS):
        last = attempt == _MAX_ATTEMPTS - 1
        try:
            response = client.post(url, json=payload, headers=headers)
        except httpx.TransportError as exc:
            if last or isinstance(exc, httpx.ReadTimeout):
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
2. target_field MUST be exactly one of the identity fields ({identity}),
   core.<KEY> where <KEY> is one of: {core_keys},
   or feature.<key> (a PROPOSED model feature, see rule 10).
   NEVER invent a core key.
3. transformation MUST be exactly one of these strings (verbatim):
   - "identity"
   - "str.strip()"
   - "to_float"
   - "to_int"
   - "parse_date"
   - "months_before(reference_date)"   (a months-of-tenure column -> observation_start)
   - "months_before_midpoint(reference_date)"  (PREFERRED for whole-month snapshot
                                        tenure: counts T months as T + 1/2, so a
                                        tenure of 0 is not a zero-length window)
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
10. A behavioral/account column that plausibly predicts churn but matches no
   core key may be PROPOSED as target_field "feature.<snake_case_key>" with
   "feature_kind": "number" (numeric measures, counts, scores) or "category"
   (labels, codes, flags). A human approves or rejects every proposal; you
   decide nothing. NEVER propose leakage/outcome columns (rule 4), IDs, free
   text, or dates as features. Merge category synonyms with map({{...}}), e.g.
   map({{'CC': 'Credit Card', 'Credit Card': 'Credit Card'}}).
"""

def _exm(
    source: str,
    target: str,
    confidence: float,
    transformation: str,
    notes: str | None,
    feature_kind: str | None = None,
) -> dict:
    example: dict[str, Any] = {
        "source_column": source,
        "target_field": target,
        "confidence": confidence,
        "transformation": transformation,
        "notes": notes,
    }
    if feature_kind is not None:
        example["feature_kind"] = feature_kind
    return example


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
            "Tenure (Months)", "observation_start", 0.9,
            "months_before_midpoint(reference_date)",
            "no signup date; whole months of tenure, counted at the interval midpoint",
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
        _exm(
            "Complaints Filed", "feature.complaints_filed", 0.7, "to_float",
            "proposed model feature: complaint count", "number",
        ),
        _exm(
            "Payment Method", "feature.payment_method", 0.6,
            "map({'CC': 'Credit Card', 'Credit Card': 'Credit Card', 'Cash': 'Cash'})",
            "proposed model feature; synonyms merged", "category",
        ),
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
        '"transformation": str, "notes": str|null, '
        '"feature_kind": "number"|"category" (feature.<key> targets only)}.',
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
    ]
    if fingerprint.primary_sheet is not None:
        lines += [
            f"Primary data sheet: {fingerprint.primary_sheet!r}. Map ONLY its columns "
            "(listed in column_names); other sheets are context, never sources.",
            "",
        ]
    lines.append("Sample rows:")
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
    match = _JSON_BLOCK.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


_NUMERIC_DTYPE = re.compile(r"^(u?int|float)\d*$", re.IGNORECASE)
_STRING_DTYPES = {"object", "string", "str"}


def _source_kind(dtype: str | None) -> str | None:
    if dtype is None:
        return None
    if _NUMERIC_DTYPE.match(dtype):
        return "number"
    if dtype.lower() in _STRING_DTYPES:
        return "string"
    return dtype.lower()


def core_type_mismatch(mapping: ProposedMapping, fingerprint: SourceFingerprint) -> str | None:
    target = mapping.target_field
    if not target.startswith("core."):
        return None
    key = target[len("core."):]
    if key not in _CORE_KEYS:
        return None
    kind = transformation_output_kind(mapping.transformation)
    if kind == "passthrough":
        kind = _source_kind(fingerprint.sample_dtypes.get(mapping.source_column))
    if kind is None:
        return None
    wanted = "string" if _core_key_type(key) == "string" else "number"
    if kind == wanted:
        return None
    return (
        f"{mapping.source_column!r} -> {target} with {mapping.transformation!r} produces "
        f"{kind} values, but {key} is a {wanted} feature"
    )


def _output_kind(mapping: ProposedMapping, fingerprint: SourceFingerprint) -> str | None:
    kind = transformation_output_kind(mapping.transformation)
    if kind == "passthrough":
        kind = _source_kind(fingerprint.sample_dtypes.get(mapping.source_column))
    return kind


def feature_target_problem(mapping: ProposedMapping, fingerprint: SourceFingerprint) -> str | None:
    key = mapping.target_field[len(_FEATURE_PREFIX):]
    if not re.fullmatch(FEATURE_KEY_PATTERN, key):
        return f"feature key {key!r} must be snake_case (letter first, at most 48 chars)"
    if key in _CORE_KEYS or key in RESERVED_FEATURE_KEYS:
        return f"feature key {key!r} clashes with a core or reserved name"
    if mapping.feature_kind is None:
        return f"{mapping.target_field} needs feature_kind 'number' or 'category'"
    kind = _output_kind(mapping, fingerprint)
    if kind == "date" or (kind is not None and kind.startswith("datetime")):
        return f"{mapping.target_field}: dates cannot be model features"
    if mapping.feature_kind == "number" and kind == "string":
        return (
            f"{mapping.source_column!r} -> {mapping.target_field} with "
            f"{mapping.transformation!r} produces text, but the feature is a number"
        )
    return None


def demote_core_type_mismatches(report: MappingReport) -> MappingReport:
    kept: list[ProposedMapping] = []
    extras = list(report.suggested_extra_features)
    flags = list(report.data_quality_flags)
    known_extras = {extra.source for extra in extras}
    for mapping in report.proposed_mappings:
        reason = core_type_mismatch(mapping, report.source_fingerprint)
        if reason is None and mapping.target_field.startswith(_FEATURE_PREFIX):
            if (
                mapping.feature_kind == "number"
                and _output_kind(mapping, report.source_fingerprint) == "string"
            ):
                mapping = mapping.model_copy(update={"feature_kind": "category"})
                flags.append(f"{mapping.target_field}: proposed as a number, kept as a category")
            reason = feature_target_problem(mapping, report.source_fingerprint)
        if reason is None:
            kept.append(mapping)
            continue
        flags.append(f"Demoted to an extra feature: {reason}")
        if mapping.source_column not in known_extras:
            known_extras.add(mapping.source_column)
            key = re.sub(r"[^0-9a-z]+", "_", mapping.source_column.lower()).strip("_")
            extras.append(
                SuggestedExtraFeature(source=mapping.source_column, suggested_key=key or "extra")
            )
    if kept == list(report.proposed_mappings):
        return report
    return report.model_copy(
        update={
            "proposed_mappings": kept,
            "suggested_extra_features": extras,
            "data_quality_flags": flags,
        }
    )


def validate_mapping_report(report: MappingReport) -> None:
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
        if mapping.feature_kind is not None and not target.startswith(_FEATURE_PREFIX):
            raise MappingReportError(
                f"feature_kind is only valid on feature.<key> targets, not {target!r}"
            )
        if target in _IDENTITY_FIELDS:
            continue
        if target.startswith(_FEATURE_PREFIX):
            problem = feature_target_problem(mapping, report.source_fingerprint)
            if problem is not None:
                raise MappingReportError(problem)
            continue
        if target.startswith("core."):
            key = target[len("core."):]
            if key not in _CORE_KEYS:
                raise MappingReportError(
                    f"target_field {target!r} is not an approved core key; core "
                    f"keys must be one of: {', '.join(sorted(_CORE_KEYS))}"
                )
            mismatch = core_type_mismatch(mapping, report.source_fingerprint)
            if mismatch is not None:
                raise MappingReportError(
                    f"{mismatch}; every record would fail Node 1's type check — "
                    "use a matching transformation or keep it as an extra feature"
                )
            continue
        raise MappingReportError(
            f"target_field {target!r} is neither an identity field "
            f"({', '.join(sorted(_IDENTITY_FIELDS))}), core.<approved key> nor "
            "feature.<key>; storage-only fields must be listed in suggested_extra_features"
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
    payload["source_fingerprint"] = fingerprint.model_dump(mode="json")
    try:
        report = MappingReport.model_validate(payload)
    except ValidationError as exc:
        raise MappingReportError(f"LLM output failed mapping-report validation: {exc}") from exc
    report = demote_core_type_mismatches(report)
    validate_mapping_report(report)
    return report


_CORE_TYPE_NAMES: dict[type, str] = {str: "string", float: "float", int: "int"}


def _core_key_type(key: str) -> str:
    annotation = CoreFeatures.model_fields[key].annotation
    for candidate in (annotation, *get_args(annotation)):
        if candidate in _CORE_TYPE_NAMES:
            return _CORE_TYPE_NAMES[candidate]
    raise MappingReportError(f"core key {key!r} has no Node 1 core type")


def derive_node1_config(
    report: MappingReport,
    base: Node1Config,
    approved_features: Sequence[ApprovedFeature] = (),
) -> Node1Config:
    keys = sorted(
        {
            mapping.target_field[len("core."):]
            for mapping in report.proposed_mappings
            if mapping.target_field.startswith("core.")
        }
    )
    return base.model_copy(
        update={
            "approved_core_keys": keys,
            "core_key_types": {key: _core_key_type(key) for key in keys},
            "allow_missing_core_passthrough": bool(approved_features),
            "declared_features": {
                feature.key: DeclaredFeature(kind=feature.kind, label=feature.label)
                for feature in sorted(approved_features, key=lambda f: f.key)
            },
        }
    )


def _default_node1_config(config_dir: Path) -> Node1Config:
    import config as config_package
    from config.loader import load_config, load_node1_config

    try:
        return load_node1_config("1", config_root=config_dir)
    except FileNotFoundError:
        shipped = Path(config_package.__file__).parent / "node1" / "v1.json"
        return load_config(shipped, Node1Config)


def _publish_node1_config(config_dir: Path, version: str, config: Node1Config) -> None:
    node1_dir = config_dir / "node1"
    node1_dir.mkdir(parents=True, exist_ok=True)
    path = node1_dir / f"v{version}.json"
    text = json.dumps(config.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    if _publish_new_file(path, text):
        return
    if path.read_text(encoding="utf-8") != text:
        raise MappingReportError(
            f"Node 1 config {path.name} already exists with different content; "
            "refusing to overwrite it"
        )


def confirm_and_persist(
    report: MappingReport,
    *,
    config_dir: Path,
    confirmed_by: str,
    confirmed_at: datetime | None = None,
    node1_config_version: str | None = None,
    approved_features: Sequence[ApprovedFeature] = (),
    supersedes: str | None = None,
) -> MappingConfig:
    validate_mapping_report(report)
    report, approved_features = _settle_features(report, approved_features)
    config_dir = Path(config_dir)
    mappings_dir = config_dir / "mappings"
    mappings_dir.mkdir(parents=True, exist_ok=True)
    confirmed_at = confirmed_at or datetime.now(UTC)
    if approved_features and node1_config_version is not None:
        raise MappingReportError(
            "approved model features need a derived Node 1 config; omit "
            "node1_config_version to derive one"
        )
    derived = (
        derive_node1_config(report, _default_node1_config(config_dir), approved_features)
        if node1_config_version is None
        else None
    )
    with _confirm_lock(mappings_dir):
        existing = find_confirmed_mapping(mappings_dir, report.source_fingerprint.headers_hash)
        if existing is not None and supersedes is None:
            raise MappingAlreadyConfirmedError(
                f"a confirmed mapping already exists for this dataset shape: {existing}; "
                "it is used automatically — confirm with supersedes to replace it",
                mapping_version=existing,
            )
        if supersedes is not None and existing != supersedes:
            raise MappingReportError(
                f"supersedes={supersedes!r} is not the active mapping for this dataset "
                f"shape (active: {existing or 'none'})"
            )
        base = "map_" + confirmed_at.strftime("%Y%m%dT%H%M%SZ")
        for suffix in range(100):
            mapping_version = base if suffix == 0 else f"{base}_{suffix}"
            path = mappings_dir / f"{mapping_version}.json"
            if path.exists():
                continue
            if derived is not None:
                _publish_node1_config(config_dir, mapping_version, derived)
            config = MappingConfig(
                mapping_version=mapping_version,
                report=report,
                confirmed_at=confirmed_at,
                confirmed_by=confirmed_by,
                node1_config_version=(
                    mapping_version if derived is not None else node1_config_version
                ),
                approved_features=list(approved_features),
                supersedes=supersedes,
            )
            text = json.dumps(config.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
            if _publish_new_file(path, text):
                return config
    raise MappingReportError(f"could not allocate a mapping file name for {base}")


_CONFIRM_THREAD_LOCK = threading.Lock()
_LOCK_STALE_S = 120.0
_LOCK_WAIT_S = 30.0


class _confirm_lock:  # noqa: N801 - used as a context manager

    def __init__(self, mappings_dir: Path) -> None:
        self._path = mappings_dir / ".confirm.lock"

    def __enter__(self) -> None:
        _CONFIRM_THREAD_LOCK.acquire()
        deadline = time.monotonic() + _LOCK_WAIT_S
        while True:
            try:
                fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    age = time.time() - self._path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age > _LOCK_STALE_S:
                    self._path.unlink(missing_ok=True)
                    continue
                if time.monotonic() > deadline:
                    _CONFIRM_THREAD_LOCK.release()
                    raise MappingReportError(
                        "another mapping confirmation is in progress; retry shortly"
                    ) from None
                time.sleep(0.05)
                continue
            os.close(fd)
            return

    def __exit__(self, *exc: object) -> None:
        try:
            self._path.unlink(missing_ok=True)
        finally:
            _CONFIRM_THREAD_LOCK.release()


def _publish_new_file(path: Path, text: str) -> bool:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        try:
            os.link(tmp, path)
        except FileExistsError:
            return False
        except OSError:
            if path.exists():
                return False
            os.replace(tmp, path)
        return True
    finally:
        tmp.unlink(missing_ok=True)


class MappingAlreadyConfirmedError(ValueError):
    def __init__(self, message: str, *, mapping_version: str) -> None:
        super().__init__(message)
        self.mapping_version = mapping_version


def find_confirmed_mapping(mappings_dir: Path, headers_hash: str) -> str | None:
    if not mappings_dir.is_dir():
        return None
    matches: list[str] = []
    superseded: set[str] = set()
    for path in sorted(mappings_dir.glob("map_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            stored_hash = payload["report"]["source_fingerprint"]["headers_hash"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if payload.get("supersedes"):
            superseded.add(str(payload["supersedes"]))
        if stored_hash == headers_hash:
            matches.append(str(payload.get("mapping_version") or path.stem))
    active = [version for version in matches if version not in superseded]
    return active[0] if active else None


def _settle_features(
    report: MappingReport, approved_features: Sequence[ApprovedFeature]
) -> tuple[MappingReport, list[ApprovedFeature]]:
    approved = {feature.key: feature for feature in approved_features}
    if len(approved) != len(approved_features):
        raise MappingReportError("approved features must have unique keys")
    proposals = {
        m.target_field[len(_FEATURE_PREFIX):]: m
        for m in report.proposed_mappings
        if m.target_field.startswith(_FEATURE_PREFIX)
    }
    for key, feature in approved.items():
        proposal = proposals.get(key)
        if proposal is None:
            raise MappingReportError(f"approved feature {key!r} has no feature.{key} mapping")
        if proposal.feature_kind != feature.kind or proposal.source_column != feature.source_column:
            raise MappingReportError(
                f"approved feature {key!r} does not match its mapping (kind/source column)"
            )
        if feature.screening.verdict == "block":
            raise MappingReportError(
                f"feature {key!r} is blocked by screening: "
                + "; ".join(feature.screening.block_reasons)
            )
    kept: list[ProposedMapping] = []
    extras = list(report.suggested_extra_features)
    known = {extra.source for extra in extras}
    for mapping in report.proposed_mappings:
        key = mapping.target_field[len(_FEATURE_PREFIX):]
        if not mapping.target_field.startswith(_FEATURE_PREFIX) or key in approved:
            kept.append(mapping)
        elif mapping.source_column not in known:
            known.add(mapping.source_column)
            extras.append(SuggestedExtraFeature(source=mapping.source_column, suggested_key=key))
    settled = report.model_copy(
        update={"proposed_mappings": kept, "suggested_extra_features": extras}
    )
    return settled, [approved[key] for key in sorted(approved)]

"""Node 1 validation hard gates (architecture §1.7, ROADMAP Task 2.7).

Runs immediately after the adapter. Record-level failures quarantine the
affected record; batch-level failures (column missingness beyond threshold,
pathological tenure distribution) stop the whole batch. Failures are structured
error dicts, never silent drops.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from config.models import Node1Config

REQUIRED_TOP_LEVEL = {
    "customer_id",
    "observation_start",
    "observation_end",
    "event_observed",
    "tenure",
    "core_features",
    "extra_features",
    "meta",
}
REQUIRED_META = {
    "source_adapter",
    "mapping_version",
    "ingested_at",
    "original_row_id",
    "reference_date",
}


@dataclass
class ValidationResult:
    """Outcome of the validation stage."""

    accepted: list[dict[str, Any]] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    batch_failed: bool = False
    missingness_passthrough: dict[str, int] = field(default_factory=dict)


#: Error codes that mean "this approved value is blank" — the only errors a
#: record may carry and still count toward the §1.7 missingness denominator.
MISSING_CODES = frozenset({"CORE_MISSING", "FEATURE_MISSING"})


def _error(code: str, message: str, record_id: str | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "record_id": record_id}


def _feature_layout(config: Node1Config) -> tuple[dict[str, str], dict[str, str]]:
    """(key -> container, declared key -> Gate 8 type) for every modeled key.

    Approved core keys live in ``core_features``; declared model features
    (architecture §1.3a) live in ``model_features`` and are typed by kind.
    Both get the same §1.7 missingness and passthrough treatment.
    """
    containers = {key: "core_features" for key in config.approved_core_keys}
    declared_types: dict[str, str] = {}
    for key, feature in config.declared_features.items():
        containers[key] = "model_features"
        declared_types[key] = "float" if feature.kind == "number" else "string"
    return containers, declared_types


def validate_records(
    records: list[dict[str, Any]],
    *,
    config: Node1Config,
    reference_date: date,
) -> ValidationResult:
    """Validate raw canonical dicts against every mandatory gate (§1.7)."""
    result = ValidationResult()
    seen_ids: set[str] = set()
    approved = set(config.approved_core_keys)
    containers, declared_types = _feature_layout(config)
    missing_counts = {key: 0 for key in containers}
    # The §1.7 batch missingness gate is evaluated over the *evaluable subset* —
    # records with no errors other than CORE_MISSING. Records already invalid for
    # an unrelated reason (bad date, duplicate ID, wrong type, ...) are quarantined
    # for that reason and must not inflate the missingness fraction and wipe out
    # the otherwise-valid records (quarantine model, not all-or-nothing).
    missing_denominator = 0
    tenure_values: list[float] = []

    # §1.7 amendment (dataset7 v1.2): when the deployment opts in, approved cores
    # missing within the threshold pass through as null cores instead of being
    # quarantined record-by-record; Node 2's complete-case rule excludes them from
    # the model matrix. Only columns *below* the threshold pass through; a column
    # above the threshold still fails the batch below, exactly as before.
    passthrough_keys = (
        _passthrough_eligible_keys(
            records,
            approved=approved,
            core_key_types=config.core_key_types,
            containers=containers,
            declared_types=declared_types,
            reference_date=reference_date,
            config=config,
        )
        if config.allow_missing_core_passthrough
        else set()
    )

    for record in records:
        stripped: dict[str, int] = {}
        for key in sorted(passthrough_keys):
            values = record.get(containers[key])
            if isinstance(values, dict) and key in values and _value_missing(
                record, key, containers
            ):
                values.pop(key)
                stripped[key] = stripped.get(key, 0) + 1
        errors = _record_errors(
            record,
            approved=approved,
            core_key_types=config.core_key_types,
            declared_types=declared_types,
            reference_date=reference_date,
            seen_ids=seen_ids,
        )
        unrelated_errors = [e for e in errors if e["code"] not in MISSING_CODES]
        if not unrelated_errors:
            missing_denominator += 1
            for key in containers:
                # Passthrough columns are already accounted for; tally only the
                # columns that still have a hard missing-value gate.
                if key not in passthrough_keys and _value_missing(record, key, containers):
                    missing_counts[key] += 1
            # Tenure sanity uses the same evaluable subset as missingness: rows
            # already quarantined (bad dates, leakage, negative tenure) must not
            # trip the batch-level outlier/zero gates and discard healthy rows.
            # (Gate 5 guarantees a finite tenure for every evaluable record.)
            tenure_values.append(float(record["tenure"]))
        if errors:
            result.rejected.append(record)
            result.errors.extend(errors)
        else:
            result.accepted.append(record)
            for key, count in stripped.items():
                result.missingness_passthrough[key] = (
                    result.missingness_passthrough.get(key, 0) + count
                )

    _column_missingness(result, missing_counts, missing_denominator, config, containers)
    _tenure_sanity(result, tenure_values, config)

    if result.batch_failed:
        result.accepted = []
        result.rejected = records
    return result


def _passthrough_eligible_keys(
    records: Sequence[dict[str, Any]],
    *,
    approved: set[str],
    core_key_types: dict[str, str],
    containers: dict[str, str],
    declared_types: dict[str, str],
    reference_date: date,
    config: Node1Config,
) -> set[str]:
    """Keys whose missingness is at or below the threshold and may pass through.

    Computed over the evaluable subset (records with no unrelated errors), the
    same denominator the batch missingness gate uses — so a key passes through
    precisely when its column would *not* fail the batch.
    """
    seen_ids: set[str] = set()
    missing_counts = {key: 0 for key in containers}
    n_evaluable = 0
    for record in records:
        errors = _record_errors(
            record,
            approved=approved,
            core_key_types=core_key_types,
            declared_types=declared_types,
            reference_date=reference_date,
            seen_ids=seen_ids,
        )
        if not [e for e in errors if e["code"] not in MISSING_CODES]:
            n_evaluable += 1
            for key in containers:
                if _value_missing(record, key, containers):
                    missing_counts[key] += 1
    if n_evaluable == 0:
        return set()
    return {
        key
        for key, count in missing_counts.items()
        if count > 0 and (count / n_evaluable) <= config.missingness_threshold
    }


def _core_value_missing(record: dict[str, Any], key: str) -> bool:
    return _value_missing(record, key, {key: "core_features"})


def _value_missing(record: dict[str, Any], key: str, containers: dict[str, str]) -> bool:
    values = record.get(containers.get(key, "core_features"))
    if not isinstance(values, dict):
        return True
    return _is_blank(values.get(key))


def _is_blank(value: Any) -> bool:
    """None, an empty/whitespace-only string, or NaN — one definition for every gate."""
    return (
        value is None
        or (isinstance(value, str) and not value.strip())
        or (isinstance(value, float) and math.isnan(value))
    )


def _record_errors(
    record: dict[str, Any],
    *,
    approved: set[str],
    core_key_types: dict[str, str],
    reference_date: date,
    seen_ids: set[str],
    declared_types: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    customer_id = record.get("customer_id")
    rid = str(customer_id) if customer_id is not None else None

    # Gate 1 — required top-level keys.
    missing_keys = REQUIRED_TOP_LEVEL - set(record.keys())
    if missing_keys:
        errors.append(
            _error("REQUIRED_KEYS", f"missing top-level keys: {sorted(missing_keys)}", rid)
        )
    meta = record.get("meta")
    if isinstance(meta, dict):
        missing_meta = REQUIRED_META - set(meta.keys())
        if missing_meta:
            errors.append(
                _error("REQUIRED_META", f"missing meta keys: {sorted(missing_meta)}", rid)
            )
    else:
        errors.append(_error("REQUIRED_META", "meta must be a dict", rid))

    # Gate 2 — customer_id non-empty.
    if customer_id is None or not str(customer_id).strip():
        errors.append(_error("CUSTOMER_ID", "customer_id must be non-empty", rid))
    else:
        if str(customer_id) in seen_ids:
            errors.append(_error("UNIQUE_ID", f"duplicate customer_id {customer_id!r}", rid))
        else:
            seen_ids.add(str(customer_id))

    # Gate 3 — valid ISO dates.
    start = _parse_iso(record.get("observation_start"))
    end = _parse_iso(record.get("observation_end"))
    if start is None:
        errors.append(_error("INVALID_DATE", "observation_start is not a valid ISO date", rid))
    if end is None:
        errors.append(_error("INVALID_DATE", "observation_end is not a valid ISO date", rid))

    # Gate 4 — window order.
    if start is not None and end is not None and start > end:
        errors.append(_error("WINDOW_ORDER", "observation_start must be <= observation_end", rid))

    # Gate 6 — event_observed must be exactly the int 0 or 1 (bool is a
    # subtype of int, so it is rejected explicitly; floats/strings are not ints).
    event = record.get("event_observed")
    if isinstance(event, bool) or not isinstance(event, int) or event not in (0, 1):
        errors.append(_error("EVENT_OBSERVED", "event_observed must be exactly 0 or 1", rid))

    # Gate 5 — tenure matches date difference.
    tenure = record.get("tenure")
    if (
        isinstance(tenure, bool)
        or not isinstance(tenure, (int, float))
        or not math.isfinite(float(tenure))
        or float(tenure) < 0
    ):
        errors.append(_error("TENURE_INVALID", "tenure must be a finite float >= 0", rid))
    elif start is not None and end is not None:
        expected = float((end - start).days)
        if float(tenure) != expected:
            errors.append(
                _error(
                    "TENURE_MISMATCH", f"tenure {tenure!r} != (end - start).days {expected}", rid
                )
            )

    # Gate 7 — no future leakage.
    if end is not None and end > reference_date:
        errors.append(_error("FUTURE_LEAKAGE", "observation_end must be <= reference_date", rid))

    # Gate 8 — approved core keys + correct types.
    core = record.get("core_features")
    if not isinstance(core, dict):
        errors.append(_error("CORE_STRUCTURE", "core_features must be a dict", rid))
        core = {}
    # Defense-in-depth: the Node 1 pipeline runs feature_gate_records BEFORE this
    # gate, demoting unapproved keys to extra_features, so this branch fires only
    # for callers that invoke validate_records directly.
    unexpected = set(core.keys()) - approved
    if unexpected:
        errors.append(_error("CORE_KEYS", f"non-approved core keys: {sorted(unexpected)}", rid))
    for key, value in core.items():
        expected_type = core_key_types.get(key, "string")
        # Same notion of "missing" as the batch missingness gate: a blank string
        # is not a category value.
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(_error("CORE_MISSING", f"core feature {key!r} is missing", rid))
        elif expected_type == "string" and not isinstance(value, str):
            errors.append(_error("CORE_TYPE", f"core feature {key!r} must be a str", rid))
        elif expected_type in {"float", "int"} and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            errors.append(_error("CORE_TYPE", f"core feature {key!r} must be a finite number", rid))

    # Gate 8b (§1.3a) — declared model features: only declared keys, same
    # missing/type rules as core (number -> finite float, category -> str).
    declared_types = declared_types or {}
    features = record.get("model_features", {})
    if not isinstance(features, dict):
        errors.append(_error("FEATURE_STRUCTURE", "model_features must be a dict", rid))
        features = {}
    undeclared = set(features) - set(declared_types)
    if undeclared:
        errors.append(
            _error("FEATURE_KEYS", f"undeclared model features: {sorted(undeclared)}", rid)
        )
    for key, value in features.items():
        if key not in declared_types:
            continue
        if _is_blank(value):
            errors.append(_error("FEATURE_MISSING", f"model feature {key!r} is missing", rid))
        elif declared_types[key] == "string" and not isinstance(value, str):
            errors.append(_error("FEATURE_TYPE", f"model feature {key!r} must be a str", rid))
        elif declared_types[key] == "float" and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            errors.append(
                _error("FEATURE_TYPE", f"model feature {key!r} must be a finite number", rid)
            )

    # Gate 10 (no NaN/inf in numeric fields) is enforced by Gate 5 for tenure and
    # by Gate 8's finiteness check for numeric cores; there is no other numeric
    # field left to check here.
    return errors


def _column_missingness(
    result: ValidationResult,
    missing_counts: dict[str, int],
    n_evaluable: int,
    config: Node1Config,
    containers: dict[str, str] | None = None,
) -> None:
    """Fail the batch when a core column is missing past the threshold.

    The fraction is computed over the *evaluable subset* (records valid except
    possibly for CORE_MISSING), not over every input row — records already
    quarantined for unrelated reasons must not push the batch over the threshold
    and discard the healthy records. With no evaluable records the gate has
    nothing to say; the batch still fails downstream if nothing is accepted.
    """
    if n_evaluable == 0:
        return
    for key, missing in missing_counts.items():
        fraction = missing / n_evaluable
        if fraction > config.missingness_threshold:
            result.errors.append(
                _error(
                    "COLUMN_MISSINGNESS",
                    f"{_describe(key, containers)} {key!r} is {fraction:.0%} missing over "
                    f"{n_evaluable} evaluable records "
                    f"(threshold {config.missingness_threshold:.0%}); batch rejected",
                )
            )
            result.batch_failed = True


def _tenure_sanity(
    result: ValidationResult, tenure_values: list[float], config: Node1Config
) -> None:
    if not tenure_values:
        return
    params = config.tenure_sanity
    n = len(tenure_values)
    zero_fraction = sum(1 for t in tenure_values if t == 0.0) / n
    if zero_fraction > params.max_zero_fraction:
        result.errors.append(
            _error(
                "TENURE_SANITY",
                f"tenure distribution dominated by zeros ({zero_fraction:.0%}); batch rejected",
            )
        )
        result.batch_failed = True
    # Robust extreme-outlier detection via median + MAD, not mean/std. A single
    # extreme value inflates the standard deviation and hides itself, so the
    # mean/std rule could never fire (architectural QA finding F-3). Median and
    # MAD are immune to that masking, so a genuine extreme tenure is actually
    # caught. When MAD is 0 (the majority of tenures are tied at the median) the
    # dispersion is degenerate: fall back to a conservative floor (about twice
    # the median, min ~10 days) so a legitimate near-tied spread — e.g. a small
    # cohort of same-window tenures — is not misread as corruption.
    median = _median(tenure_values)
    mad = _median([abs(t - median) for t in tenure_values])
    scale = mad if mad > 0 else max(median * 0.1, 1.0)
    outlier_ratio = (
        sum(1 for t in tenure_values if abs(t - median) > params.outlier_mad_factor * scale) / n
    )
    if outlier_ratio > params.max_extreme_outlier_ratio:
        result.errors.append(
            _error(
                "TENURE_SANITY",
                f"tenure distribution has {outlier_ratio:.0%} extreme outliers; batch rejected",
            )
        )
        result.batch_failed = True


def _median(values: Sequence[float]) -> float:
    """Deterministic plain-Python median (no numpy; hard rule: plain functions)."""
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


_ISO_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _parse_iso(value: Any) -> date | None:
    """Parse only the exact extended-format ISO 8601 calendar date (YYYY-MM-DD).

    ``date.fromisoformat`` on Python 3.12+ also accepts basic format
    ("20260801") and week dates ("2026-W33-1"), which ``CanonicalRecord``
    rejects — that mismatch would let a record pass Gate 3 and crash
    ``build_report`` downstream. The regex pins the accepted format so such
    values are cleanly quarantined with ``INVALID_DATE`` instead.
    """
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not _ISO_DATE_RE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _describe(key: str, containers: dict[str, str] | None) -> str:
    if containers and containers.get(key) == "model_features":
        return "model feature"
    return "core feature"

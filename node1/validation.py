"""Node 1 validation hard gates (architecture §1.7, ROADMAP Task 2.7).

Runs immediately after the adapter. Record-level failures quarantine the
affected record; batch-level failures (column missingness beyond threshold,
pathological tenure distribution) stop the whole batch. Failures are structured
error dicts, never silent drops.
"""

from __future__ import annotations

import math
import re
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


def _error(code: str, message: str, record_id: str | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "record_id": record_id}


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
    missing_counts = {key: 0 for key in approved}
    tenure_values: list[float] = []

    for record in records:
        errors = _record_errors(
            record,
            approved=approved,
            core_key_types=config.core_key_types,
            reference_date=reference_date,
            seen_ids=seen_ids,
        )
        for key in approved:
            if _core_value_missing(record, key):
                missing_counts[key] += 1
        tenure = record.get("tenure")
        if (
            isinstance(tenure, (int, float))
            and not isinstance(tenure, bool)
            and math.isfinite(float(tenure))
        ):
            tenure_values.append(float(tenure))
        if errors:
            result.rejected.append(record)
            result.errors.extend(errors)
        else:
            result.accepted.append(record)

    _column_missingness(result, missing_counts, len(records), config)
    _tenure_sanity(result, tenure_values, config)

    if result.batch_failed:
        result.accepted = []
        result.rejected = records
    return result


def _core_value_missing(record: dict[str, Any], key: str) -> bool:
    core = record.get("core_features")
    if not isinstance(core, dict):
        return True
    value = core.get(key)
    return value is None or value == ""


def _record_errors(
    record: dict[str, Any],
    *,
    approved: set[str],
    core_key_types: dict[str, str],
    reference_date: date,
    seen_ids: set[str],
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
        if value is None:
            errors.append(_error("CORE_MISSING", f"core feature {key!r} is missing", rid))
        elif expected_type == "string" and not isinstance(value, str):
            errors.append(_error("CORE_TYPE", f"core feature {key!r} must be a str", rid))
        elif expected_type in {"float", "int"} and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            errors.append(_error("CORE_TYPE", f"core feature {key!r} must be a finite number", rid))

    # Gate 10 — no NaN/inf in numeric fields.
    if (
        isinstance(tenure, (int, float))
        and not isinstance(tenure, bool)
        and not math.isfinite(float(tenure))
    ):
        errors.append(_error("NON_FINITE", "tenure must not be NaN/inf", rid))

    return errors


def _column_missingness(
    result: ValidationResult,
    missing_counts: dict[str, int],
    n_records: int,
    config: Node1Config,
) -> None:
    if n_records == 0:
        return
    for key, missing in missing_counts.items():
        fraction = missing / n_records
        if fraction > config.missingness_threshold:
            result.errors.append(
                _error(
                    "COLUMN_MISSINGNESS",
                    f"core feature {key!r} is {fraction:.0%} missing "
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
    mean = sum(tenure_values) / n
    std = math.sqrt(sum((t - mean) ** 2 for t in tenure_values) / n)
    if std > 0:
        outlier_ratio = (
            sum(1 for t in tenure_values if abs(t - mean) > params.outlier_std_factor * std) / n
        )
        if outlier_ratio > params.max_extreme_outlier_ratio:
            result.errors.append(
                _error(
                    "TENURE_SANITY",
                    f"tenure distribution has {outlier_ratio:.0%} extreme outliers; batch rejected",
                )
            )
            result.batch_failed = True


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

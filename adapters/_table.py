"""Shared row-to-record helpers for deterministic adapters.

Adapters build *row maps* (canonical-shaped field values) and hand them to
``rows_to_records``, which applies tenure/censoring via ``BaseAdapter``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pandas as pd

from adapters.base import BaseAdapter

REQUIRED_COLUMNS = {"customer_id", "observation_start", "observation_end", "event_observed"}
CORE_COLUMNS = {"plan_tier", "contract_length_months", "usage_frequency"}
_EXTRA_EXCLUDED = REQUIRED_COLUMNS | CORE_COLUMNS


def coerce_string(value: Any) -> str | None:
    """Coerce a value to a stripped string, or ``None`` when blank.

    Pandas reads blank cells as NaN, whose ``str()`` is the literal ``"nan"`` —
    treat it (and every other NA sentinel: ``pd.NA``, ``NaT``, numpy floats) as
    missing, never as a real value. A literal ``"nan"`` string from the source
    is preserved (``pd.isna`` is False for it).
    """
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def iter_rows(frame: pd.DataFrame) -> Iterator[tuple[Any, dict[str, Any]]]:
    """Yield ``(index, {column: value})`` with each column's own value type.

    ``DataFrame.iterrows`` (and ``frame.values``) upcast an all-numeric table to
    float64, so an integer id ``1`` became ``"1.0"`` and stopped joining with
    support data. Casting to ``object`` keeps every value as its column holds it;
    for mixed-type tables it yields exactly what ``iterrows`` did.
    """
    columns = list(frame.columns)
    for index, row_values in zip(frame.index, frame.astype(object).values, strict=True):
        yield index, dict(zip(columns, row_values, strict=True))


def first_present(*values: Any) -> Any:
    """The first value that is not missing (pandas NaN is truthy, so ``a or b`` fails)."""
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            if value.strip():
                return value
            continue
        if not pd.isna(value):
            return value
    return None


def obvious_row_maps(frame: Any) -> list[dict[str, Any]]:
    """Row maps for a table whose columns are already canonical names."""
    from adapters.util import status_to_event, to_float, to_int

    row_maps: list[dict[str, Any]] = []
    for index, values in iter_rows(frame):
        event = status_to_event(values.get("event_observed"))
        if event is None:
            event = to_int(values.get("event_observed"))
        row_maps.append(
            {
                "customer_id": coerce_string(values.get("customer_id")),
                "observation_start": values.get("observation_start"),
                "observation_end": values.get("observation_end"),
                "event_observed": event,
                "core_features": {
                    "plan_tier": coerce_string(values.get("plan_tier")),
                    "contract_length_months": to_float(values.get("contract_length_months")),
                    "usage_frequency": to_float(values.get("usage_frequency")),
                },
                "extra_features": {
                    key: value for key, value in values.items() if key not in _EXTRA_EXCLUDED
                },
                "original_row_id": str(index),
            }
        )
    return row_maps


def rows_to_records(
    adapter: BaseAdapter, row_maps: list[dict[str, Any]], reference_date: str
) -> list[dict]:
    """Convert prepared row maps into canonical record dicts."""
    records: list[dict[str, Any]] = []
    for row_map in row_maps:
        records.append(
            adapter.build_record(
                customer_id=row_map.get("customer_id"),
                observation_start=row_map.get("observation_start"),
                observation_end_raw=row_map.get("observation_end"),
                event_observed=row_map.get("event_observed"),
                core_features=row_map.get("core_features", {}),
                extra_features=row_map.get("extra_features", {}),
                reference_date=reference_date,
                original_row_id=row_map.get("original_row_id"),
            )
        )
    return records

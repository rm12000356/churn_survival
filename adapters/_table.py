from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pandas as pd

from adapters.base import BaseAdapter

REQUIRED_COLUMNS = {"customer_id", "observation_start", "observation_end", "event_observed"}
CORE_COLUMNS = {"plan_tier", "contract_length_months", "usage_frequency"}
_EXTRA_EXCLUDED = REQUIRED_COLUMNS | CORE_COLUMNS


def coerce_string(value: Any) -> str | None:
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def iter_rows(frame: pd.DataFrame) -> Iterator[tuple[Any, dict[str, Any]]]:
    columns = list(frame.columns)
    for index, row_values in zip(frame.index, frame.astype(object).values, strict=True):
        yield index, dict(zip(columns, row_values, strict=True))


def first_present(*values: Any) -> Any:
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

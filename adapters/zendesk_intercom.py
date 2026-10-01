"""Deterministic adapter for Zendesk/Intercom-style dumps (architecture §1.5, ROADMAP Task 2.4).

Matches a ``Tickets``/``Conversations`` sheet, or a table carrying support
columns (``ticket_id``+``requester_id`` or ``conversation_id``+``user_id``).
Used as a canonicalization shape; status values are mapped via the shared
vocabulary (``canceled``/``churned`` -> churned, everything else active).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from adapters._table import coerce_string, first_present, iter_rows, rows_to_records
from adapters.base import BaseAdapter
from adapters.util import status_to_event, to_float

SUPPORT_SHEETS = {"tickets", "conversations", "ticket", "conversation"}
_ZENDESK_COLUMNS = {"ticket_id", "requester_id"}
_INTERCOM_COLUMNS = {"conversation_id", "user_id"}
_CONSUMED = {
    "ticket_id",
    "requester_id",
    "conversation_id",
    "user_id",
    "created_at",
    "solved_at",
    "closed_at",
    "status",
    "plan",
    "plan_tier",
    "contract_length_months",
    "usage_frequency",
}


class ZendeskIntercomAdapter(BaseAdapter):
    """Parses a Zendesk/Intercom-style support dump."""

    name = "zendesk_intercom"
    version = "1.0.0"
    confidence = 0.92
    priority = 50

    def matches_signature(self, fingerprint: Any) -> bool:
        if any(s.lower() in SUPPORT_SHEETS for s in fingerprint.sheet_names):
            return True
        columns = set(fingerprint.column_names)
        return _ZENDESK_COLUMNS.issubset(columns) or _INTERCOM_COLUMNS.issubset(columns)

    def _frame(self, raw_data: Any) -> pd.DataFrame:
        if isinstance(raw_data, dict):
            for sheet in raw_data:
                if sheet.lower() in SUPPORT_SHEETS:
                    return raw_data[sheet]
            raise TypeError(f"{self.name} matched a workbook but found no {SUPPORT_SHEETS} sheet")
        if isinstance(raw_data, pd.DataFrame):
            return raw_data
        raise TypeError(
            f"{self.name} expects a DataFrame or workbook, got {type(raw_data).__name__}"
        )

    def transform(self, raw_data: Any, reference_date: str) -> list[dict]:
        frame = self._frame(raw_data)
        row_maps: list[dict[str, Any]] = []
        for index, values in iter_rows(frame):
            event = status_to_event(values.get("status"))
            if event is None and coerce_string(values.get("status")) is None:
                # Blank means "no churn recorded" (censored). An unrecognised
                # non-blank value stays None so validation quarantines the row
                # instead of silently entering the model as censored.
                event = 0
            row_maps.append(
                {
                    "customer_id": coerce_string(
                        first_present(values.get("requester_id"), values.get("user_id"))
                    ),
                    "observation_start": values.get("created_at"),
                    "observation_end": first_present(
                        values.get("solved_at"), values.get("closed_at")
                    ),
                    "event_observed": event,
                    "core_features": {
                        "plan_tier": coerce_string(
                            first_present(values.get("plan_tier"), values.get("plan"))
                        ),
                        "contract_length_months": to_float(values.get("contract_length_months")),
                        "usage_frequency": to_float(values.get("usage_frequency")),
                    },
                    "extra_features": {
                        key: value for key, value in values.items() if key not in _CONSUMED
                    },
                    "original_row_id": str(index),
                }
            )
        return rows_to_records(self, row_maps, reference_date)

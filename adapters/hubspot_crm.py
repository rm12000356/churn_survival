"""Deterministic adapter for HubSpot-style CRM exports (architecture §1.5, ROADMAP Task 2.4).

Matches a workbook with a ``Contacts`` sheet or a table carrying HubSpot contact
columns (``email``, ``firstname`` + ``lifecyclestage``/``hs_object_id``). A
``lifecyclestage`` of ``customer-offboarding``/``lost`` maps to churned.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from adapters._table import coerce_string, first_present, iter_rows, rows_to_records
from adapters.base import BaseAdapter
from adapters.util import status_to_event, to_float

CONTACT_SHEETS = {"contacts", "contact"}
_REQUIRED_COLUMNS = {"email", "firstname"}
_STRUCTURE_CUES = {"lifecyclestage", "hs_object_id"}
_CONSUMED = {
    "email",
    "hs_object_id",
    "firstname",
    "lastname",
    "hs_createdate",
    "createdate",
    "hs_closedate",
    "churn_date",
    "lifecyclestage",
    "plan",
    "plan_tier",
    "contract_length_months",
    "usage_frequency",
}


class HubspotCrmAdapter(BaseAdapter):
    """Parses a HubSpot CRM export."""

    name = "hubspot_crm"
    version = "1.1.0"
    confidence = 0.92
    priority = 40

    def matches_signature(self, fingerprint: Any) -> bool:
        if any(s.lower() in CONTACT_SHEETS for s in fingerprint.sheet_names):
            return True
        columns = set(fingerprint.column_names)
        return _REQUIRED_COLUMNS.issubset(columns) and bool(_STRUCTURE_CUES.intersection(columns))

    def _frame(self, raw_data: Any) -> pd.DataFrame:
        if isinstance(raw_data, dict):
            for sheet in raw_data:
                if sheet.lower() in CONTACT_SHEETS:
                    return raw_data[sheet]
            raise TypeError(f"{self.name} matched a workbook but found no {CONTACT_SHEETS} sheet")
        if isinstance(raw_data, pd.DataFrame):
            return raw_data
        raise TypeError(
            f"{self.name} expects a DataFrame or workbook, got {type(raw_data).__name__}"
        )

    def transform(self, raw_data: Any, reference_date: str) -> list[dict]:
        frame = self._frame(raw_data)
        row_maps: list[dict[str, Any]] = []
        for index, values in iter_rows(frame):
            event = status_to_event(values.get("lifecyclestage"))
            if event is None and coerce_string(values.get("lifecyclestage")) is None:
                # Blank means "no churn recorded" (censored). An unrecognised
                # non-blank value stays None so validation quarantines the row
                # instead of silently entering the model as censored.
                event = 0
            row_maps.append(
                {
                    "customer_id": coerce_string(
                        first_present(values.get("email"), values.get("hs_object_id"))
                    ),
                    "observation_start": first_present(
                        values.get("hs_createdate"), values.get("createdate")
                    ),
                    "observation_end": first_present(
                        values.get("hs_closedate"), values.get("churn_date")
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

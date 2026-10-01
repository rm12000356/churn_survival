"""Deterministic adapter for Stripe-style customer + subscription exports.

Architecture §1.5, ROADMAP Task 2.4.

Matches a workbook with a ``Subscriptions`` sheet or a single table carrying
Stripe subscription columns (``customer``, ``status``, ``current_period_start``).
Churn = subscription ``canceled`` (event 1); everything else is censored at the
declared ``reference_date``.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from adapters._table import coerce_string, first_present, iter_rows, rows_to_records
from adapters.base import BaseAdapter
from adapters.util import status_to_event, to_float

SUBSCRIPTION_SHEETS = {"subscriptions", "subscription"}
REQUIRED_COLUMNS = {"customer", "status", "current_period_start"}
_INTERVAL_MONTHS = {
    "month": 1.0,
    "monthly": 1.0,
    "year": 12.0,
    "yearly": 12.0,
}
_CONSUMED = {
    "customer",
    "status",
    "current_period_start",
    "current_period_end",
    "canceled_at",
    "plan",
    "plan_id",
    "interval",
    "interval_months",
    "usage_frequency",
}


class StripeCustomersAdapter(BaseAdapter):
    """Parses a Stripe customer + subscription export."""

    name = "stripe_customers"
    version = "1.0.0"
    confidence = 0.97
    priority = 30

    def matches_signature(self, fingerprint: Any) -> bool:
        if any(s.lower() in SUBSCRIPTION_SHEETS for s in fingerprint.sheet_names):
            return True
        return REQUIRED_COLUMNS.issubset(set(fingerprint.column_names))

    def _frame(self, raw_data: Any) -> pd.DataFrame:
        if isinstance(raw_data, dict):
            for sheet in raw_data:
                if sheet.lower() in SUBSCRIPTION_SHEETS:
                    return raw_data[sheet]
            raise TypeError(
                f"{self.name} matched a workbook but found no {SUBSCRIPTION_SHEETS} sheet"
            )
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
            end = values.get("canceled_at") if event == 1 else values.get("current_period_end")
            interval_months = to_float(values.get("interval_months"))
            # Weekly (or unknown) billing intervals have no whole-month contract
            # length: left missing rather than mis-stated as one month.
            if interval_months is None and coerce_string(values.get("interval")):
                interval_months = _INTERVAL_MONTHS.get(str(values["interval"]).strip().lower())
            row_maps.append(
                {
                    "customer_id": coerce_string(values.get("customer")),
                    "observation_start": values.get("current_period_start"),
                    "observation_end": end,
                    "event_observed": event,
                    "core_features": {
                        "plan_tier": coerce_string(
                            first_present(values.get("plan"), values.get("plan_id"))
                        ),
                        "contract_length_months": interval_months,
                        "usage_frequency": to_float(values.get("usage_frequency")),
                    },
                    "extra_features": {
                        key: value for key, value in values.items() if key not in _CONSUMED
                    },
                    "original_row_id": str(index),
                }
            )
        return rows_to_records(self, row_maps, reference_date)

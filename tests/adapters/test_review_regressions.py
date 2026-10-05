"""Adapter regressions for findings M-I2, M-I3, M-I4, L15, L17."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from adapters._table import iter_rows
from adapters.hubspot_crm import HubspotCrmAdapter
from adapters.mapping_adapter import apply_transformation, is_allowed_transformation
from adapters.stripe_customers import StripeCustomersAdapter

REFERENCE_DATE = "2026-08-15"


def _hubspot(**overrides: object) -> pd.DataFrame:
    row = {
        "email": "a@example.com",
        "firstname": "Ada",
        "hs_object_id": "501",
        "lifecyclestage": "customer",
        "hs_createdate": "2025-01-01",
        "createdate": "2024-12-01",
        "hs_closedate": None,
        "churn_date": None,
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_hubspot_falls_back_when_first_column_is_blank() -> None:
    """M-I4: NaN is truthy, so ``a or b`` never fell back."""
    frame = _hubspot(email=float("nan"), hs_createdate=float("nan"))
    record = HubspotCrmAdapter().transform(frame, REFERENCE_DATE)[0]
    assert record["customer_id"] == "501"
    assert record["observation_start"] == "2024-12-01"


def test_hubspot_unknown_lifecycle_stage_is_not_censored() -> None:
    record = HubspotCrmAdapter().transform(_hubspot(lifecyclestage="chrned"), REFERENCE_DATE)[0]
    assert record["event_observed"] is None


def test_all_numeric_table_keeps_integer_ids() -> None:
    """M-I3: iterrows upcast an all-numeric table, turning id 1 into "1.0"."""
    frame = pd.DataFrame({"id": [1, 2], "score": [0.5, 1.5]})
    rows = [values for _index, values in iter_rows(frame)]
    assert rows[0]["id"] == 1 and not isinstance(rows[0]["id"], float)
    assert rows[1]["score"] == 1.5


@pytest.mark.parametrize(
    "transform",
    ["map(not a dict)", "map({[1]: 2})", "map(" + "[" * 5000 + "]" * 5000 + ")"],
)
def test_malformed_map_literals_are_rejected_not_raised(transform: str) -> None:
    """M-I2: every literal_eval failure is a validation 'no', never a crash."""
    assert is_allowed_transformation(transform) is False


def test_map_lookup_never_matches_blank_cells() -> None:
    """L15: a NaN cell must not match a "nan" key."""
    assert apply_transformation(math.nan, 'map({"nan": 1, "yes": 1})') is None
    assert apply_transformation("Yes", 'map({"yes": 1})') == 1


def test_weekly_stripe_interval_is_not_a_month() -> None:
    """L17: a weekly interval has no whole-month contract length."""
    frame = pd.DataFrame(
        [
            {
                "customer": "cus_1",
                "status": "active",
                "current_period_start": "2026-01-01",
                "current_period_end": "2026-08-01",
                "interval": "week",
            }
        ]
    )
    record = StripeCustomersAdapter().transform(frame, REFERENCE_DATE)[0]
    assert record["core_features"]["contract_length_months"] is None

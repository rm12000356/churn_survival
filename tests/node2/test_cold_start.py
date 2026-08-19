"""Cold-start / per-customer state classification (ROADMAP Task 3.8/§2.9)."""

from __future__ import annotations

from node2.cold_start import classify_customer, classify_customers
from schemas.enums import CustomerState
from tests.node2.conftest import PREDICTORS, make_config, make_record


def test_zero_tenure_is_not_enough_data() -> None:
    record = make_record(1, tenure=0, event=0)
    assert classify_customer(record, PREDICTORS, make_config()) == CustomerState.NOT_ENOUGH_DATA


def test_no_usable_predictors_is_excluded() -> None:
    record = make_record(
        1, tenure=200, event=0, plan_tier=None, usage_frequency=None, contract_length_months=None
    )
    assert classify_customer(record, PREDICTORS, make_config()) == CustomerState.EXCLUDED


def test_complete_short_tenure_with_too_few_features_is_not_enough_data() -> None:
    config = make_config(cold_start_min_behavioral_features=4)
    record = make_record(1, tenure=10, event=0)
    assert classify_customer(record, PREDICTORS, config) == CustomerState.NOT_ENOUGH_DATA


def test_incomplete_long_tenure_is_excluded() -> None:
    record = make_record(1, tenure=200, event=0, plan_tier=None, usage_frequency=10.0)
    assert classify_customer(record, PREDICTORS, make_config()) == CustomerState.EXCLUDED


def test_complete_record_is_scored() -> None:
    record = make_record(1, tenure=200, event=0)
    assert classify_customer(record, PREDICTORS, make_config()) == CustomerState.SCORED


def test_incomplete_near_zero_tenure_cold_start() -> None:
    config = make_config(cold_start_min_behavioral_features=3)
    record = make_record(1, tenure=10, event=0, plan_tier=None, usage_frequency=10.0)
    assert classify_customer(record, PREDICTORS, config) == CustomerState.NOT_ENOUGH_DATA


def test_incomplete_long_tenure_stays_excluded_even_with_min_features() -> None:
    config = make_config(cold_start_min_behavioral_features=3)
    record = make_record(1, tenure=200, event=0, plan_tier=None, usage_frequency=10.0)
    assert classify_customer(record, PREDICTORS, config) == CustomerState.EXCLUDED


def test_short_tenure_complete_is_still_scored_when_features_sufficient() -> None:
    config = make_config(cold_start_min_behavioral_features=3)
    record = make_record(1, tenure=10, event=0)
    assert classify_customer(record, PREDICTORS, config) == CustomerState.SCORED


def test_classify_customers_batch() -> None:
    records = [
        make_record(1, tenure=0, event=0),
        make_record(2, tenure=100, event=0),
        make_record(3, tenure=100, event=0, plan_tier=None, usage_frequency=None),
    ]
    states = classify_customers(records, PREDICTORS, make_config())
    assert states["cus_0001"] == CustomerState.NOT_ENOUGH_DATA
    assert states["cus_0002"] == CustomerState.SCORED
    assert states["cus_0003"] == CustomerState.EXCLUDED

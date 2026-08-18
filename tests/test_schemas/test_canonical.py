from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.canonical import CanonicalRecord
from tests.conftest import make_active_customer, make_churned_customer


def test_active_customer_is_valid() -> None:
    record = CanonicalRecord.model_validate(make_active_customer())
    assert record.tenure == 521.0
    assert record.event_observed == 0
    assert record.meta.reference_date.isoformat() == "2026-08-15"


def test_churned_customer_is_valid() -> None:
    record = CanonicalRecord.model_validate(make_churned_customer())
    assert record.tenure == 472.0
    assert record.event_observed == 1


def test_extra_core_key_rejected() -> None:
    record = make_active_customer()
    record["core_features"]["unapproved_feature"] = 1.0
    with pytest.raises(ValidationError):
        CanonicalRecord.model_validate(record)


def test_negative_tenure_rejected() -> None:
    record = make_active_customer()
    record["tenure"] = -1.0
    with pytest.raises(ValidationError):
        CanonicalRecord.model_validate(record)


def test_infinite_tenure_rejected() -> None:
    record = make_active_customer()
    record["tenure"] = float("inf")
    with pytest.raises(ValidationError):
        CanonicalRecord.model_validate(record)


def test_tenure_must_equal_date_diff() -> None:
    record = make_active_customer()
    record["tenure"] = 500.0
    with pytest.raises(ValidationError):
        CanonicalRecord.model_validate(record)


def test_event_observed_must_be_0_or_1() -> None:
    record = make_active_customer()
    record["event_observed"] = 2
    with pytest.raises(ValidationError):
        CanonicalRecord.model_validate(record)


def test_non_iso_date_rejected() -> None:
    record = make_active_customer()
    record["observation_start"] = "03/12/2025"
    with pytest.raises(ValidationError):
        CanonicalRecord.model_validate(record)


def test_observation_start_after_end_rejected() -> None:
    record = make_active_customer()
    record["observation_start"] = "2026-08-16"
    with pytest.raises(ValidationError):
        CanonicalRecord.model_validate(record)


def test_extra_top_level_key_rejected() -> None:
    record = make_active_customer()
    record["tenure_start_date"] = "2025-03-12"
    with pytest.raises(ValidationError):
        CanonicalRecord.model_validate(record)


def test_missing_reference_date_rejected() -> None:
    record = make_active_customer()
    del record["meta"]["reference_date"]
    with pytest.raises(ValidationError):
        CanonicalRecord.model_validate(record)


def test_empty_customer_id_rejected() -> None:
    record = make_active_customer()
    record["customer_id"] = ""
    with pytest.raises(ValidationError):
        CanonicalRecord.model_validate(record)


def test_extra_features_are_open() -> None:
    record = make_active_customer()
    record["extra_features"]["nested_dict"] = {"a": [1, 2, 3]}
    record["extra_features"]["list_value"] = [1, "x"]
    CanonicalRecord.model_validate(record)


def test_round_trip_preserves_fields() -> None:
    record = CanonicalRecord.model_validate(make_churned_customer())
    dumped = record.model_dump(mode="json")
    assert dumped["customer_id"] == "cus_9d2e4f7a"
    assert dumped["observation_start"] == "2024-11-03"
    assert dumped["meta"]["mapping_version"] == "map_2026-08-12T14:22:00Z"

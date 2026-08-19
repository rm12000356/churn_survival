from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from schemas.enums import ValidationStatus
from schemas.validation import Node1Output, ValidationReport

REPORT = {
    "status": "PASSED",
    "n_input_rows": 2,
    "n_accepted": 2,
    "n_rejected": 0,
    "errors": [],
    "warnings": [],
    "adapter_used": "clean_csv",
    "mapping_version": "map_v1",
    "reference_date": "2026-08-15",
}


def test_valid_passed_report() -> None:
    report = ValidationReport.model_validate(REPORT)
    assert report.status is ValidationStatus.PASSED
    assert report.reference_date == date(2026, 8, 15)


def test_all_statuses_accepted() -> None:
    for status in ("PASSED", "FAILED", "PARTIAL"):
        payload = {**REPORT, "status": status}
        assert ValidationReport.model_validate(payload).status == status


def test_invalid_status_rejected() -> None:
    payload = {**REPORT, "status": "SUCCESS"}
    with pytest.raises(ValidationError):
        ValidationReport.model_validate(payload)


def test_negative_counts_rejected() -> None:
    payload = {**REPORT, "n_accepted": -1}
    with pytest.raises(ValidationError):
        ValidationReport.model_validate(payload)


def test_extra_key_rejected() -> None:
    payload = {**REPORT, "unexpected": 1}
    with pytest.raises(ValidationError):
        ValidationReport.model_validate(payload)


def test_node1_output_round_trip() -> None:
    from tests.conftest import make_active_customer

    output = Node1Output.model_validate(
        {
            "canonical_dataset": [make_active_customer()],
            "validation_report": REPORT,
        }
    )
    assert len(output.canonical_dataset) == 1
    assert output.validation_report.n_accepted == 2


def test_demoted_features_optional_and_round_trips() -> None:
    report = ValidationReport.model_validate(REPORT)
    assert report.demoted_features == {}

    payload = {**REPORT, "demoted_features": {"region": 12, "plan_tier": 3}}
    report = ValidationReport.model_validate(payload)
    assert report.demoted_features == {"region": 12, "plan_tier": 3}

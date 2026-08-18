from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from node1.report import build_report
from node1.validation import ValidationResult
from schemas.enums import ValidationStatus
from tests.conftest import make_active_customer, make_churned_customer

REFERENCE_DATE = date(2026, 8, 15)


def _result(
    accepted: list[dict], rejected: list[dict], errors: list[dict], batch_failed: bool = False
):
    return ValidationResult(
        accepted=accepted, rejected=rejected, errors=errors, batch_failed=batch_failed
    )


def _all_pass() -> tuple[list[dict[str, Any]], ValidationResult]:
    records = [make_active_customer(), make_churned_customer()]
    return records, _result(records, [], [])


def test_passed_status() -> None:
    records, result = _all_pass()
    output = build_report(
        records,
        result,
        adapter_name="clean_csv",
        mapping_version="clean_csv_v1.0.0",
        reference_date=REFERENCE_DATE,
    )
    assert output.validation_report.status is ValidationStatus.PASSED
    assert output.validation_report.n_accepted == 2
    assert output.validation_report.n_rejected == 0
    assert len(output.canonical_dataset) == 2
    assert output.canonical_dataset[0].meta.reference_date == REFERENCE_DATE


def test_partial_status_keeps_accepted() -> None:
    bad = dict(make_active_customer())
    bad["customer_id"] = "  "
    records = [make_active_customer(), bad]
    result = _result(
        [records[0]], [bad], [{"code": "CUSTOMER_ID", "message": "x", "record_id": None}]
    )
    output = build_report(
        records,
        result,
        adapter_name="clean_csv",
        mapping_version="m",
        reference_date=REFERENCE_DATE,
    )
    assert output.validation_report.status is ValidationStatus.PARTIAL
    assert output.validation_report.n_accepted == 1
    assert output.validation_report.n_rejected == 1
    assert len(output.canonical_dataset) == 1


def test_failed_batch_yields_empty_dataset() -> None:
    records = [make_active_customer()]
    result = _result(
        [],
        records,
        [{"code": "COLUMN_MISSINGNESS", "message": "x", "record_id": None}],
        batch_failed=True,
    )
    output = build_report(
        records,
        result,
        adapter_name="clean_csv",
        mapping_version="m",
        reference_date=REFERENCE_DATE,
    )
    assert output.validation_report.status is ValidationStatus.FAILED
    assert output.canonical_dataset == []


def test_failed_complete_validation_yields_empty_dataset() -> None:
    bad = dict(make_active_customer())
    bad["event_observed"] = 2
    records = [bad]
    result = _result([], records, [{"code": "EVENT_OBSERVED", "message": "x", "record_id": None}])
    output = build_report(
        records,
        result,
        adapter_name="clean_csv",
        mapping_version="m",
        reference_date=REFERENCE_DATE,
    )
    assert output.validation_report.status is ValidationStatus.FAILED
    assert output.canonical_dataset == []


def test_empty_input_is_failed() -> None:
    output = build_report(
        [],
        _result([], [], []),
        adapter_name="clean_csv",
        mapping_version="m",
        reference_date=REFERENCE_DATE,
    )
    assert output.validation_report.status is ValidationStatus.FAILED


def test_warnings_and_versions_recorded() -> None:
    records, result = _all_pass()
    output = build_report(
        records,
        result,
        adapter_name="hubspot_crm",
        mapping_version="hubspot_crm_v1.0.0",
        reference_date=REFERENCE_DATE,
        warnings=["usage_frequency correlates with contract_length_months"],
    )
    assert output.validation_report.warnings == [
        "usage_frequency correlates with contract_length_months"
    ]
    assert output.validation_report.adapter_used == "hubspot_crm"
    assert output.validation_report.mapping_version == "hubspot_crm_v1.0.0"


def test_invalid_accepted_record_raises_runtime_error() -> None:
    bad = dict(make_active_customer())
    bad["event_observed"] = 2  # would fail CanonicalRecord
    records = [make_active_customer(), bad]
    result = ValidationResult(accepted=[bad], rejected=[records[0]], errors=[], batch_failed=False)
    with pytest.raises(RuntimeError, match="accepted record failed"):
        build_report(
            records,
            result,
            adapter_name="clean_csv",
            mapping_version="m",
            reference_date=REFERENCE_DATE,
        )

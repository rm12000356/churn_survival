from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.mapping import MappingReport
from tests.conftest import make_mapping_report


def test_mapping_report_round_trip() -> None:
    report = MappingReport.model_validate(make_mapping_report())
    assert report.source_fingerprint.n_sample_rows == 25
    assert len(report.proposed_mappings) == 4
    assert report.recommended_action == "create_deterministic_adapter"
    assert report.llm_model_used.startswith("anthropic")


def test_headers_hash_requires_sha256_length() -> None:
    report = make_mapping_report()
    report["source_fingerprint"]["headers_hash"] = "short"
    with pytest.raises(ValidationError):
        MappingReport.model_validate(report)


def test_confidence_out_of_range_rejected() -> None:
    report = make_mapping_report()
    report["proposed_mappings"][0]["confidence"] = 1.5
    with pytest.raises(ValidationError):
        MappingReport.model_validate(report)


def test_extra_key_rejected() -> None:
    report = make_mapping_report()
    report["llm_direct_transformation"] = True
    with pytest.raises(ValidationError):
        MappingReport.model_validate(report)


def test_open_dicts_accept_any_dtypes() -> None:
    report = make_mapping_report()
    report["source_fingerprint"]["sample_dtypes"]["Extra"] = "object"
    MappingReport.model_validate(report)

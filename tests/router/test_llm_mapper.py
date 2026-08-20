from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from adapters.mapping_adapter import (
    MappingConfigAdapter,
    apply_transformation,
    load_confirmed_mapping_adapters,
)
from config.loader import load_config
from config.models import MappingConfig
from router.fingerprint import extract_fingerprint
from router.llm_mapper import (
    MappingReportError,
    build_mapping_prompt,
    confirm_and_persist,
    extract_json,
    generate_mapping_report,
)
from schemas.canonical import CanonicalRecord
from schemas.mapping import MappingReport
from tests.mapping_helpers import FakeClient, mapping_payload

FIXTURES = Path(__file__).parent.parent / "adapters" / "fixtures"


def _mapping_payload(fingerprint) -> dict:
    return mapping_payload(fingerprint)


@pytest.fixture
def unmapped_frame() -> pd.DataFrame:
    return pd.read_csv(FIXTURES / "unmapped_export.csv")


@pytest.fixture
def unmapped_fingerprint(unmapped_frame):
    return extract_fingerprint(unmapped_frame)


def test_mapping_report_schema_round_trip(unmapped_fingerprint) -> None:
    report = MappingReport.model_validate(_mapping_payload(unmapped_fingerprint))
    assert report.recommended_action == "create_deterministic_adapter"
    assert report.llm_model_used == "test/fake"


def test_build_mapping_prompt_includes_headers_and_sample(
    unmapped_frame, unmapped_fingerprint
) -> None:
    prompt = build_mapping_prompt(unmapped_fingerprint, unmapped_frame, n_rows=5)
    assert "Cust ID" in prompt
    assert "ACME-1" in prompt
    assert "headers_hash" in prompt


def test_extract_json_strips_fences() -> None:
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json('{"a": 1}') == '{"a": 1}'


def test_generate_mapping_report_validates_output(unmapped_frame, unmapped_fingerprint) -> None:
    client = FakeClient(json.dumps(_mapping_payload(unmapped_fingerprint)))
    report = generate_mapping_report(unmapped_fingerprint, unmapped_frame, client=client)
    assert isinstance(report, MappingReport)
    assert report.llm_model_used == "test/fake"


def test_generate_mapping_report_records_configured_model_not_self_reported(
    tmp_path: Path, unmapped_frame, unmapped_fingerprint
) -> None:
    payload = _mapping_payload(unmapped_fingerprint)
    payload["llm_model_used"] = "hallucinated/model-name"
    client = FakeClient(json.dumps(payload), model="configured/prod-model")
    report = generate_mapping_report(unmapped_fingerprint, unmapped_frame, client=client)
    assert report.llm_model_used == "configured/prod-model"

    config = confirm_and_persist(report, config_dir=tmp_path, confirmed_by="reviewer")
    reloaded = load_config(tmp_path / "mappings" / f"{config.mapping_version}.json", MappingConfig)
    assert reloaded.report.llm_model_used == "configured/prod-model"


def test_generate_mapping_report_rejects_non_json(unmapped_frame, unmapped_fingerprint) -> None:
    client = FakeClient("this is not json")
    with pytest.raises(MappingReportError):
        generate_mapping_report(unmapped_fingerprint, unmapped_frame, client=client)


def test_generate_mapping_report_rejects_invalid_schema(
    unmapped_frame, unmapped_fingerprint
) -> None:
    payload = _mapping_payload(unmapped_fingerprint)
    payload["proposed_mappings"][0]["confidence"] = 3.0  # out of [0,1]
    client = FakeClient(json.dumps(payload))
    with pytest.raises(MappingReportError):
        generate_mapping_report(unmapped_fingerprint, unmapped_frame, client=client)


def test_confirm_and_persist_then_deterministic_re_run(
    tmp_path: Path, unmapped_frame, unmapped_fingerprint
) -> None:
    report = MappingReport.model_validate(_mapping_payload(unmapped_fingerprint))
    confirmed_at = datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC)
    config = confirm_and_persist(
        report, config_dir=tmp_path, confirmed_by="reviewer", confirmed_at=confirmed_at
    )
    assert isinstance(config, MappingConfig)

    stored_path = tmp_path / "mappings" / f"{config.mapping_version}.json"
    assert stored_path.is_file()
    reloaded = load_config(stored_path, MappingConfig)
    assert reloaded.mapping_version == config.mapping_version

    adapters = load_confirmed_mapping_adapters(tmp_path)
    assert len(adapters) == 1
    mapping_adapter = adapters[0]
    assert isinstance(mapping_adapter, MappingConfigAdapter)
    assert mapping_adapter.matches_signature(unmapped_fingerprint)

    records = mapping_adapter.transform(unmapped_frame, "2026-08-15")
    assert len(records) == 2
    by_id = {r["customer_id"]: r for r in records}
    assert by_id["ACME-1"]["event_observed"] == 1
    assert by_id["ACME-2"]["event_observed"] == 0
    assert by_id["ACME-2"]["observation_end"] == "2026-08-15"
    assert by_id["ACME-1"]["core_features"]["plan_tier"] == "Enterprise"
    for record in records:
        assert CanonicalRecord.model_validate(record).customer_id


def test_load_confirmed_mappings_empty_dir(tmp_path: Path) -> None:
    assert load_confirmed_mapping_adapters(tmp_path) == []


def test_duplicate_headers_hash_configs_fail_loudly(tmp_path: Path) -> None:
    frame = pd.read_csv(FIXTURES / "unmapped_export.csv")
    fingerprint = extract_fingerprint(frame)
    report = MappingReport.model_validate(mapping_payload(fingerprint))
    confirm_and_persist(
        report, config_dir=tmp_path, confirmed_by="r1", confirmed_at=datetime(2026, 8, 17, 9, 0, 0)
    )
    confirm_and_persist(
        report, config_dir=tmp_path, confirmed_by="r2", confirmed_at=datetime(2026, 8, 17, 9, 0, 1)
    )
    with pytest.raises(ValueError, match="duplicate mapping configs"):
        load_confirmed_mapping_adapters(tmp_path)


def test_apply_transformation_audited_subset() -> None:
    assert apply_transformation("  abc  ", "str.strip()") == "abc"
    assert apply_transformation("12.5", "to_float") == 12.5
    assert apply_transformation("Churned", "map({'Churned': 1, 'Active': 0})") == 1
    assert apply_transformation(None, "parse_date") is None
    assert apply_transformation("12", "months_before(reference_date)", date(2026, 8, 15)) == date(
        2026, 8, 15
    ) - timedelta(days=round(12 * 30.4375))
    assert apply_transformation("x", "snapshot_end(reference_date)", date(2026, 8, 15)) == date(
        2026, 8, 15
    )


def test_apply_transformation_unknown_op_raises() -> None:
    with pytest.raises(ValueError, match="unrecognized transformation"):
        apply_transformation("raw", "some_unknown_op")
    with pytest.raises(ValueError, match="unrecognized transformation"):
        apply_transformation("raw", "parse_date; if null use reference_date")


def test_apply_transformation_row_number_raises() -> None:
    with pytest.raises(ValueError, match="customer_id"):
        apply_transformation("x", "row_number")


def test_is_allowed_transformation() -> None:
    from adapters.mapping_adapter import is_allowed_transformation

    assert is_allowed_transformation(None)
    assert is_allowed_transformation("")
    assert is_allowed_transformation("identity")
    assert is_allowed_transformation("str.strip()")
    assert is_allowed_transformation("strip")
    assert is_allowed_transformation("to_float")
    assert is_allowed_transformation("to_int")
    assert is_allowed_transformation("parse_date")
    assert is_allowed_transformation("months_before(reference_date)")
    assert is_allowed_transformation("snapshot_end(reference_date)")
    assert is_allowed_transformation("row_number")
    assert is_allowed_transformation("map({'Churned': 1, 'Active': 0})")
    assert not is_allowed_transformation("parse_date(mixed_formats=True)")
    assert not is_allowed_transformation("parse_date; if null use reference_date")
    assert not is_allowed_transformation("months_before(ref)")
    assert not is_allowed_transformation("snapshot_end")
    assert not is_allowed_transformation("map([1, 2])")
    assert not is_allowed_transformation("exec('x')")


def test_shipped_confirmed_configs_pass_strict_validation() -> None:
    from adapters.mapping_adapter import load_confirmed_mapping_adapters
    from router.llm_mapper import validate_mapping_report

    adapters = load_confirmed_mapping_adapters(Path("config"))
    if not adapters:
        pytest.skip("no confirmed mapping configs shipped")
    for adapter in adapters:
        validate_mapping_report(adapter._config.report)


def test_validate_mapping_report_rejects_unknown_transform(unmapped_fingerprint) -> None:
    from router.llm_mapper import validate_mapping_report

    payload = _mapping_payload(unmapped_fingerprint)
    payload["proposed_mappings"][1]["transformation"] = "parse_date; if null use reference_date"
    report = MappingReport.model_validate(payload)
    with pytest.raises(MappingReportError, match="invalid mapping"):
        validate_mapping_report(report)


def test_validate_mapping_report_rejects_non_union_core_key(unmapped_fingerprint) -> None:
    from router.llm_mapper import validate_mapping_report

    payload = _mapping_payload(unmapped_fingerprint)
    payload["proposed_mappings"].append(
        {
            "source_column": "Plan Name",
            "target_field": "core.credit_score",
            "confidence": 0.9,
            "transformation": "to_float",
            "notes": None,
        }
    )
    report = MappingReport.model_validate(payload)
    with pytest.raises(MappingReportError, match="not an approved core key"):
        validate_mapping_report(report)


def test_validate_mapping_report_rejects_bare_extra_target(unmapped_fingerprint) -> None:
    from router.llm_mapper import validate_mapping_report

    payload = _mapping_payload(unmapped_fingerprint)
    payload["proposed_mappings"].append(
        {
            "source_column": "Plan Name",
            "target_field": "notes_text",
            "confidence": 0.9,
            "transformation": "str.strip()",
            "notes": None,
        }
    )
    report = MappingReport.model_validate(payload)
    with pytest.raises(MappingReportError, match="suggested_extra_features"):
        validate_mapping_report(report)


def test_validate_mapping_report_rejects_row_number_on_non_customer_id(
    unmapped_fingerprint,
) -> None:
    from router.llm_mapper import validate_mapping_report

    payload = _mapping_payload(unmapped_fingerprint)
    payload["proposed_mappings"].append(
        {
            "source_column": "Cust ID",
            "target_field": "observation_start",
            "confidence": 0.9,
            "transformation": "row_number",
            "notes": None,
        }
    )
    report = MappingReport.model_validate(payload)
    with pytest.raises(MappingReportError, match="only valid for target_field 'customer_id'"):
        validate_mapping_report(report)


def test_generate_mapping_report_rejects_non_whitelisted_transform(
    unmapped_frame, unmapped_fingerprint
) -> None:
    payload = _mapping_payload(unmapped_fingerprint)
    payload["proposed_mappings"][1]["transformation"] = "parse_date; if null use reference_date"
    client = FakeClient(json.dumps(payload))
    with pytest.raises(MappingReportError, match="invalid mapping"):
        generate_mapping_report(unmapped_fingerprint, unmapped_frame, client=client)


def test_confirm_and_persist_rejects_invalid_report(
    tmp_path: Path, unmapped_fingerprint
) -> None:
    payload = _mapping_payload(unmapped_fingerprint)
    payload["proposed_mappings"].append(
        {
            "source_column": "Plan Name",
            "target_field": "core.credit_score",
            "confidence": 0.9,
            "transformation": "to_float",
            "notes": None,
        }
    )
    report = MappingReport.model_validate(payload)
    with pytest.raises(MappingReportError, match="not an approved core key"):
        confirm_and_persist(report, config_dir=tmp_path, confirmed_by="reviewer")


def test_apply_transformation_rejects_non_dict_map() -> None:
    with pytest.raises(ValueError):
        apply_transformation("x", "map([1, 2])")

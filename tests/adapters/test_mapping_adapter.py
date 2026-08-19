from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from adapters.mapping_adapter import (
    MappingConfigAdapter,
    apply_transformation,
    load_confirmed_mapping_adapters,
)
from router.fingerprint import extract_fingerprint
from router.llm_mapper import confirm_and_persist
from schemas.mapping import MappingReport
from tests.mapping_helpers import mapping_payload

REFERENCE_DATE = "2026-08-15"
FIXTURES = Path(__file__).parent.parent / "adapters" / "fixtures"


def _fixture_frame(name: str) -> pd.DataFrame:
    return pd.read_csv(FIXTURES / name)


def _confirmed_adapter(
    frame: pd.DataFrame, tmp_path: Path, payload: dict | None = None
) -> tuple[MappingConfigAdapter, dict]:
    payload = payload or mapping_payload(extract_fingerprint(frame))
    report = MappingReport.model_validate(payload)
    config = confirm_and_persist(report, config_dir=tmp_path, confirmed_by="reviewer")
    adapter = MappingConfigAdapter(config)
    return adapter, payload


def test_apply_transformation_edge_cases() -> None:
    assert apply_transformation("x", None) == "x"
    assert apply_transformation("5", "to_int") == 5
    assert apply_transformation(None, "map({'A': 1})") is None
    assert apply_transformation("ACTIVE", "map({'Active': 0, 'Churned': 1})") == 0
    assert apply_transformation("Nope", "map({'Active': 0, 'Churned': 1})") is None


def test_apply_transformation_case_insensitive_key_match() -> None:
    assert apply_transformation("churned", "map({'Churned': 1, 'Active': 0})") == 1


def test_apply_transformation_months_before() -> None:
    ref = date(2026, 8, 15)
    assert apply_transformation(12, "months_before(reference_date)", ref) == ref - timedelta(
        days=round(12 * 30.4375)
    )
    assert apply_transformation(0, "months_before(reference_date)", ref) == ref
    assert apply_transformation(None, "months_before(reference_date)", ref) is None
    assert apply_transformation("junk", "months_before(reference_date)", ref) is None
    assert apply_transformation(12, "months_before(reference_date)") is None
    assert apply_transformation(12, "snapshot_end(reference_date)", ref) == ref
    assert apply_transformation(None, "snapshot_end(reference_date)", ref) == ref
    assert apply_transformation(12, "snapshot_end(reference_date)") is None


def test_transform_row_number_assigns_deterministic_ids(tmp_path: Path) -> None:
    frame = _fixture_frame("unmapped_export.csv")
    fingerprint = extract_fingerprint(frame)
    payload = mapping_payload(fingerprint)
    payload["proposed_mappings"] = [
        m for m in payload["proposed_mappings"] if m["target_field"] != "customer_id"
    ]
    payload["proposed_mappings"].append(
        {
            "source_column": "CustomerID",
            "target_field": "customer_id",
            "confidence": 0.99,
            "transformation": "row_number",
            "notes": "no customer ID column",
        }
    )

    adapter, _ = _confirmed_adapter(frame, tmp_path, payload)
    records = adapter.transform(frame, REFERENCE_DATE)

    assert [r["customer_id"] for r in records] == ["0", "1"]


def test_transform_months_before_derives_observation_start(tmp_path: Path) -> None:
    frame = _fixture_frame("unmapped_export.csv")
    fingerprint = extract_fingerprint(frame)
    payload = mapping_payload(fingerprint)
    payload["proposed_mappings"] = [
        m
        for m in payload["proposed_mappings"]
        if m["target_field"] not in {"observation_start", "observation_end"}
    ]
    for target, transformation in (
        ("observation_start", "months_before(reference_date)"),
        ("observation_end", "snapshot_end(reference_date)"),
    ):
        payload["proposed_mappings"].append(
            {
                "source_column": "Tenure Months",
                "target_field": target,
                "confidence": 0.9,
                "transformation": transformation,
                "notes": "snapshot, no churn date",
            }
        )
    frame = frame.copy()
    frame["Tenure Months"] = [12, 3]

    adapter, _ = _confirmed_adapter(frame, tmp_path, payload)
    records = adapter.transform(frame, REFERENCE_DATE)

    ref = date.fromisoformat(REFERENCE_DATE)
    assert (
        records[0]["observation_start"] == (ref - timedelta(days=round(12 * 30.4375))).isoformat()
    )
    assert records[1]["observation_start"] == (ref - timedelta(days=round(3 * 30.4375))).isoformat()
    assert records[0]["observation_end"] == REFERENCE_DATE  # churned, censored at ref_date
    assert records[0]["tenure"] == round(12 * 30.4375)
    assert records[1]["tenure"] == round(3 * 30.4375)


def test_frame_workbook_sheet_selection(tmp_path: Path) -> None:
    frame = _fixture_frame("unmapped_export.csv")
    fingerprint = extract_fingerprint(frame)
    payload = mapping_payload(fingerprint)
    payload["source_fingerprint"]["sheet_names"] = ["Sheet1"]
    adapter, _ = _confirmed_adapter(frame, tmp_path, payload)

    workbook = {"Sheet1": frame, "Other": frame.copy()}
    assert adapter._frame(workbook) is frame

    fallback = {"Other": frame}
    assert adapter._frame(fallback) is frame


def test_frame_rejects_unknown_input_type(tmp_path: Path) -> None:
    frame = _fixture_frame("unmapped_export.csv")
    adapter, _ = _confirmed_adapter(frame, tmp_path)
    with pytest.raises(TypeError, match="expects a DataFrame or workbook"):
        adapter._frame("nope")


def test_transform_stores_unmapped_columns_in_extra(tmp_path: Path) -> None:
    frame = _fixture_frame("unmapped_export.csv")
    adapter, _ = _confirmed_adapter(frame, tmp_path)
    frame = frame.copy()
    frame["Notes"] = ["one", "two"]
    records = adapter.transform(frame, REFERENCE_DATE)
    assert records[0]["extra_features"]["Notes"] == "one"
    assert records[1]["extra_features"]["Notes"] == "two"


def test_transform_normalizes_string_event(tmp_path: Path) -> None:
    frame = _fixture_frame("unmapped_export.csv")
    fingerprint = extract_fingerprint(frame)
    payload = mapping_payload(fingerprint)
    for mapping in payload["proposed_mappings"]:
        if mapping["target_field"] == "event_observed":
            mapping["transformation"] = "str.strip()"
    adapter, _ = _confirmed_adapter(frame, tmp_path, payload)

    records = adapter.transform(frame, REFERENCE_DATE)
    by_id = {r["customer_id"]: r for r in records}
    assert by_id["ACME-1"]["event_observed"] == 1  # "Churned"
    assert by_id["ACME-2"]["event_observed"] == 0  # "Active" (via to_int fallback path)


def test_transform_numeric_string_event_via_strip(tmp_path: Path) -> None:
    frame = _fixture_frame("unmapped_export.csv")
    fingerprint = extract_fingerprint(frame)
    payload = mapping_payload(fingerprint)
    for mapping in payload["proposed_mappings"]:
        if mapping["target_field"] == "event_observed":
            mapping["transformation"] = "str.strip()"
    adapter, _ = _confirmed_adapter(frame, tmp_path, payload)

    frame = frame.copy()
    frame["Status"] = ["1", "0"]  # numeric strings, not status words
    records = adapter.transform(frame, REFERENCE_DATE)

    assert len(records) == 2
    by_id = {r["customer_id"]: r for r in records}
    assert by_id["ACME-1"]["event_observed"] == 1
    assert by_id["ACME-2"]["event_observed"] == 0


def test_transform_categorical_event_map(tmp_path: Path) -> None:
    frame = _fixture_frame("unmapped_export.csv")
    fingerprint = extract_fingerprint(frame)
    payload = mapping_payload(fingerprint)
    for mapping in payload["proposed_mappings"]:
        if mapping["target_field"] == "event_observed":
            mapping["transformation"] = "map({'Attrited Customer': 1, 'Existing Customer': 0})"
    adapter, _ = _confirmed_adapter(frame, tmp_path, payload)

    frame = frame.copy()
    frame["Status"] = ["Existing Customer", "Attrited Customer"]
    records = adapter.transform(frame, REFERENCE_DATE)
    assert records[0]["event_observed"] == 0
    assert records[1]["event_observed"] == 1


def test_transform_routes_suggested_extra_feature(tmp_path: Path) -> None:
    frame = _fixture_frame("unmapped_export.csv")
    fingerprint = extract_fingerprint(frame)
    payload = mapping_payload(fingerprint)
    payload["suggested_extra_features"].append(
        {"source": "Plan Name", "suggested_key": "notes_text"}
    )
    adapter, _ = _confirmed_adapter(frame, tmp_path, payload)
    records = adapter.transform(frame, REFERENCE_DATE)
    assert records[0]["extra_features"]["notes_text"] == "Enterprise"


def test_transform_preserves_numeric_core_values(tmp_path: Path) -> None:
    frame = _fixture_frame("unmapped_export.csv")
    fingerprint = extract_fingerprint(frame)
    payload = mapping_payload(fingerprint)
    payload["proposed_mappings"].append(
        {
            "source_column": "Usage Freq",
            "target_field": "core.monthly_charges",
            "confidence": 0.9,
            "transformation": "to_float",
            "notes": None,
        }
    )
    adapter, _ = _confirmed_adapter(frame, tmp_path, payload)
    records = adapter.transform(frame, REFERENCE_DATE)
    assert records[0]["core_features"]["monthly_charges"] == 33.0
    assert isinstance(records[0]["core_features"]["monthly_charges"], float)


def test_transform_mapping_e2e_round_trip(tmp_path: Path) -> None:
    frame = _fixture_frame("unmapped_export.csv")
    adapter, _ = _confirmed_adapter(frame, tmp_path)
    records = adapter.transform(frame, REFERENCE_DATE)
    assert len(records) == 2
    by_id = {r["customer_id"]: r for r in records}
    assert by_id["ACME-1"]["event_observed"] == 1
    assert by_id["ACME-2"]["observation_end"] == REFERENCE_DATE


def test_adapter_mapping_version_and_signature(tmp_path: Path) -> None:
    frame = _fixture_frame("unmapped_export.csv")
    adapter, _ = _confirmed_adapter(frame, tmp_path)
    fingerprint = extract_fingerprint(frame)
    assert adapter.matches_signature(fingerprint) is True
    config = adapter.get_mapping_config()
    assert config["mapping_version"] == adapter.mapping_version
    assert json.dumps(config)  # serializable


def test_load_confirmed_adapters_from_directory(tmp_path: Path) -> None:
    frame = _fixture_frame("unmapped_export.csv")
    adapter, _ = _confirmed_adapter(frame, tmp_path)
    loaded = load_confirmed_mapping_adapters(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].name == adapter.name

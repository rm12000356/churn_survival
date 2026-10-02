"""A confirmed mapping is enough to run a new format.

Regression for two onboarding gaps found on a real e-commerce workbook:

* the data sheet is the one with the most rows, not the first sheet (a workbook
  that opens with a data dictionary used to fingerprint the dictionary);
* confirming a mapping without an explicit Node 1 config derives one from the
  mapping, instead of runs falling back to ``v1`` and failing the whole batch on
  core keys the dataset never had.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from adapters.mapping_adapter import MappingConfigAdapter
from config.loader import load_node1_config
from config.models import MappingConfig
from router.fingerprint import extract_fingerprint, primary_sheet_name
from router.llm_mapper import (
    MappingReportError,
    build_mapping_prompt,
    confirm_and_persist,
    derive_node1_config,
)
from schemas.mapping import MappingReport, SourceFingerprint
from tests.mapping_helpers import mapping_payload

FIXTURES = Path(__file__).parent.parent / "adapters" / "fixtures"


def _data() -> pd.DataFrame:
    return pd.read_csv(FIXTURES / "unmapped_export.csv")


def _dictionary() -> pd.DataFrame:
    # Fewer rows than the 2-row data fixture (a real dictionary is ~20 vs thousands).
    return pd.DataFrame({"Unnamed: 0": [None], "Variable": ["Cust ID"], "Meaning": ["id"]})


def _workbook() -> dict[str, pd.DataFrame]:
    return {"Data Dict": _dictionary(), "Customers Export": _data()}


def identity_only_payload(fingerprint: SourceFingerprint) -> dict:
    """``mapping_payload`` without its ``core.*`` targets (identity fields only)."""
    payload = mapping_payload(fingerprint)
    payload["proposed_mappings"] = [
        m for m in payload["proposed_mappings"] if not m["target_field"].startswith("core.")
    ]
    return payload


# --- Fix A: the data sheet ---------------------------------------------------


def test_primary_sheet_is_the_largest_not_the_first() -> None:
    fingerprint = extract_fingerprint(_workbook())
    assert fingerprint.sheet_names == ["Data Dict", "Customers Export"]
    assert fingerprint.primary_sheet == "Customers Export"
    assert fingerprint.column_names == list(_data().columns)


def test_primary_sheet_tie_goes_to_the_earlier_sheet() -> None:
    frame = pd.DataFrame({"a": [1, 2]})
    assert primary_sheet_name({"First": frame, "Second": frame.copy()}) == "First"


def test_single_table_has_no_primary_sheet() -> None:
    assert extract_fingerprint(_data()).primary_sheet is None


def test_workbook_fingerprint_matches_the_same_data_as_csv() -> None:
    # Same data sheet, so the same shape: a mapping confirmed for one routes both.
    workbook, single = extract_fingerprint(_workbook()), extract_fingerprint(_data())
    assert workbook.headers_hash == single.headers_hash


def test_legacy_fingerprint_without_primary_sheet_still_loads() -> None:
    payload = extract_fingerprint(_workbook()).model_dump(mode="json")
    payload.pop("primary_sheet")
    assert SourceFingerprint.model_validate(payload).primary_sheet is None


def _adapter(fingerprint: SourceFingerprint) -> MappingConfigAdapter:
    config = MappingConfig(
        mapping_version="map_test",
        report=MappingReport.model_validate(mapping_payload(fingerprint)),
        confirmed_at=datetime(2026, 8, 17, 10, 0, tzinfo=UTC),
        confirmed_by="reviewer",
    )
    return MappingConfigAdapter(config)


def test_mapping_adapter_reads_the_primary_sheet() -> None:
    workbook = _workbook()
    records = _adapter(extract_fingerprint(workbook)).transform(workbook, "2026-08-15")
    assert len(records) == len(_data())
    assert records[0]["customer_id"] == "ACME-1"


def test_mapping_adapter_legacy_mapping_reads_first_known_sheet() -> None:
    fingerprint = extract_fingerprint(_workbook()).model_copy(
        update={"primary_sheet": None, "sheet_names": ["Customers Export"]}
    )
    workbook = {"Customers Export": _data(), "Notes": pd.DataFrame({"n": [1]})}
    records = _adapter(fingerprint).transform(workbook, "2026-08-15")
    assert len(records) == len(_data())


def test_prompt_names_the_primary_sheet() -> None:
    workbook = _workbook()
    prompt = build_mapping_prompt(extract_fingerprint(workbook), workbook)
    assert "Primary data sheet: 'Customers Export'" in prompt
    assert "Primary data sheet" not in build_mapping_prompt(extract_fingerprint(_data()), _data())


# --- Fix B: the derived Node 1 config -------------------------------------------


def test_derived_config_without_core_targets_requires_no_core_keys() -> None:
    report = MappingReport.model_validate(identity_only_payload(extract_fingerprint(_data())))
    base = load_node1_config("1")
    derived = derive_node1_config(report, base)
    assert derived.approved_core_keys == []
    assert derived.core_key_types == {}
    assert derived.tenure_sanity == base.tenure_sanity
    assert derived.missingness_threshold == base.missingness_threshold
    assert derived.validation_version == base.validation_version


def test_derived_config_types_core_targets_from_core_features() -> None:
    report = MappingReport.model_validate(mapping_payload(extract_fingerprint(_data())))
    derived = derive_node1_config(report, load_node1_config("1"))
    assert derived.approved_core_keys == ["contract_length_months", "plan_tier", "usage_frequency"]
    assert derived.core_key_types == {
        "contract_length_months": "float",
        "plan_tier": "string",
        "usage_frequency": "float",
    }


def test_confirm_without_version_writes_and_records_derived_config(tmp_path: Path) -> None:
    report = MappingReport.model_validate(identity_only_payload(extract_fingerprint(_data())))
    config = confirm_and_persist(report, config_dir=tmp_path, confirmed_by="reviewer")
    assert config.node1_config_version == config.mapping_version
    assert (tmp_path / "node1" / f"v{config.mapping_version}.json").is_file()
    loaded = load_node1_config(config.mapping_version, config_root=tmp_path)
    assert loaded.approved_core_keys == []
    stored = json.loads((tmp_path / "mappings" / f"{config.mapping_version}.json").read_text())
    assert stored["node1_config_version"] == config.mapping_version


def test_confirm_with_explicit_version_writes_no_derived_config(tmp_path: Path) -> None:
    report = MappingReport.model_validate(mapping_payload(extract_fingerprint(_data())))
    config = confirm_and_persist(
        report, config_dir=tmp_path, confirmed_by="reviewer", node1_config_version="telco"
    )
    assert config.node1_config_version == "telco"
    assert not (tmp_path / "node1").exists()


def test_confirm_never_overwrites_a_different_node1_config(tmp_path: Path) -> None:
    clash = tmp_path / "node1" / "vmap_20260817T100000Z.json"
    clash.parent.mkdir(parents=True)
    clash.write_text("{}\n", encoding="utf-8")
    report = MappingReport.model_validate(identity_only_payload(extract_fingerprint(_data())))
    with pytest.raises(MappingReportError, match="refusing to overwrite"):
        confirm_and_persist(
            report,
            config_dir=tmp_path,
            confirmed_by="reviewer",
            confirmed_at=datetime(2026, 8, 17, 10, 0, tzinfo=UTC),
        )
    assert clash.read_text(encoding="utf-8") == "{}\n"
    assert not list((tmp_path / "mappings").glob("map_*.json"))


def test_load_node1_config_root_cannot_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_node1_config("../x", config_root=tmp_path)


def test_load_node1_config_root_falls_back_to_settings(tmp_path: Path) -> None:
    # A mappings-only config dir still resolves shipped configs such as v1.
    assert load_node1_config("1", config_root=tmp_path) == load_node1_config("1")


# --- Core type check: an LLM guess is demoted, a human mismatch is rejected ---------


def _with_core(fingerprint: SourceFingerprint, *mappings: dict) -> dict:
    payload = identity_only_payload(fingerprint)
    payload["proposed_mappings"] += list(mappings)
    payload["suggested_extra_features"] = []
    return payload


def _core(source: str, key: str, transformation: str) -> dict:
    return {
        "source_column": source,
        "target_field": f"core.{key}",
        "confidence": 0.9,
        "transformation": transformation,
        "notes": None,
    }


@pytest.mark.parametrize(
    ("transformation", "kind"),
    [
        ("identity", "passthrough"),
        ("str.strip()", "passthrough"),
        ("to_int", "number"),
        ("to_float", "number"),
        ("parse_date", "date"),
        ("months_before(reference_date)", "date"),
        ("map({'a': 'x', 'b': 'y'})", "string"),
        ("map({'a': 1, 'b': 2.5})", "number"),
        ("map({'a': 1, 'b': 'y'})", None),
    ],
)
def test_transformation_output_kind(transformation: str, kind: str | None) -> None:
    from adapters.mapping_adapter import transformation_output_kind

    assert transformation_output_kind(transformation) == kind


def test_llm_wrong_typed_core_guess_is_demoted_to_extras() -> None:
    from router.llm_mapper import demote_core_type_mismatches

    fingerprint = extract_fingerprint(_data())
    report = MappingReport.model_validate(
        _with_core(
            fingerprint,
            _core("Contract Months", "plan_tier", "to_int"),  # number into a string key
            _core("Usage Freq", "usage_frequency", "to_float"),  # fine
        )
    )
    demoted = demote_core_type_mismatches(report)
    targets = [m.target_field for m in demoted.proposed_mappings]
    assert "core.plan_tier" not in targets
    assert "core.usage_frequency" in targets
    assert [e.source for e in demoted.suggested_extra_features] == ["Contract Months"]
    assert any("plan_tier" in flag for flag in demoted.data_quality_flags)
    # The repaired report is valid and derives a config without plan_tier.
    derived = derive_node1_config(demoted, load_node1_config("1"))
    assert derived.approved_core_keys == ["usage_frequency"]


def test_llm_report_generation_demotes_instead_of_failing() -> None:
    from router.llm_mapper import generate_mapping_report
    from tests.mapping_helpers import FakeClient

    fingerprint = extract_fingerprint(_data())
    payload = _with_core(fingerprint, _core("Contract Months", "plan_tier", "to_int"))
    report = generate_mapping_report(fingerprint, _data(), client=FakeClient(json.dumps(payload)))
    assert all(m.target_field != "core.plan_tier" for m in report.proposed_mappings)


def test_human_wrong_typed_core_mapping_is_rejected(tmp_path: Path) -> None:
    fingerprint = extract_fingerprint(_data())
    report = MappingReport.model_validate(
        _with_core(fingerprint, _core("Plan Name", "usage_frequency", "identity"))
    )
    with pytest.raises(MappingReportError, match="usage_frequency is a number feature"):
        confirm_and_persist(report, config_dir=tmp_path, confirmed_by="reviewer")
    assert not (tmp_path / "mappings").exists() or not list(
        (tmp_path / "mappings").glob("map_*.json")
    )


def test_core_mapping_with_matching_type_is_untouched() -> None:
    from router.llm_mapper import demote_core_type_mismatches

    report = MappingReport.model_validate(mapping_payload(extract_fingerprint(_data())))
    assert demote_core_type_mismatches(report) is report

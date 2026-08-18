from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from adapters.mapping_adapter import load_confirmed_mapping_adapters
from config.models import Node1Config
from node1.node import UnmappedFormatError, main, run_mapping_workflow, run_node1
from router.fingerprint import extract_fingerprint
from router.llm_mapper import confirm_and_persist
from schemas.enums import ValidationStatus
from schemas.mapping import MappingReport
from tests.mapping_helpers import FakeClient, fake_report_text, mapping_payload

FIXTURES = Path(__file__).parent.parent / "adapters" / "fixtures"
REFERENCE_DATE = date(2026, 8, 15)


def test_e2e_clean_csv(fresh_settings, node1_config: Node1Config) -> None:
    output = run_node1(
        FIXTURES / "clean_customers.csv",
        reference_date=REFERENCE_DATE,
        config=node1_config,
    )
    assert output.validation_report.status is ValidationStatus.PASSED
    assert output.validation_report.adapter_used == "clean_csv"
    assert output.validation_report.matched_candidates == ["clean_csv"]
    assert output.validation_report.n_accepted == 3
    assert len(output.canonical_dataset) == 3
    assert output.validation_report.reference_date == REFERENCE_DATE


def test_e2e_stripe_routes_and_validates(fresh_settings, node1_config: Node1Config) -> None:
    output = run_node1(
        FIXTURES / "stripe_export.csv",
        reference_date=REFERENCE_DATE,
        config=node1_config,
    )
    assert output.validation_report.adapter_used == "stripe_customers"
    assert output.validation_report.status is ValidationStatus.PASSED


def test_e2e_xlsx_workbook(fresh_settings, node1_config: Node1Config, tmp_path: Path) -> None:
    customers = pd.read_csv(FIXTURES / "clean_customers.csv")
    invoices = pd.DataFrame({"invoice_id": ["INV-1"], "amount": [100.0]})
    workbook = tmp_path / "workbook.xlsx"
    with pd.ExcelWriter(workbook, engine="openpyxl") as writer:
        customers.to_excel(writer, sheet_name="Customers", index=False)
        invoices.to_excel(writer, sheet_name="Invoices", index=False)

    output = run_node1(
        workbook,
        reference_date=REFERENCE_DATE,
        config=node1_config,
    )
    assert output.validation_report.adapter_used == "excel_multi_sheet"
    assert output.validation_report.status is ValidationStatus.PASSED
    assert len(output.canonical_dataset) == 3


def test_e2e_unmapped_raises(fresh_settings, node1_config: Node1Config) -> None:
    with pytest.raises(UnmappedFormatError) as exc_info:
        run_node1(
            FIXTURES / "unmapped_export.csv",
            reference_date=REFERENCE_DATE,
            config=node1_config,
        )
    assert exc_info.value.fingerprint is not None


def test_run_mapping_workflow_returns_report(fresh_settings) -> None:
    frame = pd.read_csv(FIXTURES / "unmapped_export.csv")
    frame_text = fake_report_text(extract_fingerprint(frame))
    report = run_mapping_workflow(
        FIXTURES / "unmapped_export.csv",
        client=FakeClient(frame_text),
    )
    assert isinstance(report, MappingReport)
    assert report.llm_model_used == "test/fake"


def test_confirmed_mapping_takes_deterministic_path(
    fresh_settings, node1_config: Node1Config, tmp_path: Path
) -> None:
    frame = pd.read_csv(FIXTURES / "unmapped_export.csv")
    fingerprint = extract_fingerprint(frame)
    report = MappingReport.model_validate(mapping_payload(fingerprint))
    confirm_and_persist(report, config_dir=tmp_path, confirmed_by="reviewer")

    adapters = load_confirmed_mapping_adapters(tmp_path)
    assert len(adapters) == 1

    output = run_node1(
        FIXTURES / "unmapped_export.csv",
        reference_date=REFERENCE_DATE,
        config=node1_config,
        adapters=adapters,
    )
    assert output.validation_report.status is ValidationStatus.PASSED
    assert output.validation_report.adapter_used.startswith("mapping:")
    assert len(output.canonical_dataset) == 2
    ids = {record.customer_id for record in output.canonical_dataset}
    assert ids == {"ACME-1", "ACME-2"}


def test_cli_main_success(fresh_settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(FIXTURES / "clean_customers.csv")]) == 0
    assert "status=PASSED" in capsys.readouterr().out


def test_cli_main_unmapped_format(fresh_settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(FIXTURES / "unmapped_export.csv")]) == 1
    err = capsys.readouterr().err
    assert "no deterministic adapter matched" in err
    assert "docs/onboarding.md" in err


def test_map_main_draft_skeleton(fresh_settings, capsys, tmp_path: Path) -> None:
    from node1.node import map_main
    from schemas.mapping import MappingReport

    out = tmp_path / "draft.json"
    assert map_main([str(FIXTURES / "unmapped_export.csv"), "--out", str(out)]) == 0
    draft = MappingReport.model_validate_json(out.read_text(encoding="utf-8"))
    assert (
        draft.source_fingerprint.headers_hash
        == extract_fingerprint(pd.read_csv(FIXTURES / "unmapped_export.csv")).headers_hash
    )
    assert draft.proposed_mappings == []
    assert draft.unmapped_columns == draft.source_fingerprint.column_names
    assert draft.recommended_action == "create_deterministic_adapter"
    assert draft.llm_model_used == "manual/template"
    assert "--confirm" in capsys.readouterr().out


def test_map_main_confirm_roundtrip(fresh_settings, monkeypatch, capsys, tmp_path: Path) -> None:
    import config.settings as cs
    from config.models import MappingConfig
    from node1.node import map_main
    from schemas.mapping import MappingReport, ProposedMapping

    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(cs, "_settings", None)
    out = tmp_path / "draft.json"
    assert map_main([str(FIXTURES / "unmapped_export.csv"), "--out", str(out)]) == 0
    draft = MappingReport.model_validate_json(out.read_text(encoding="utf-8"))
    draft.proposed_mappings = [
        ProposedMapping(
            source_column="gender",
            target_field="core.plan_tier",
            confidence=0.99,
            transformation="identity",
        )
    ]
    out.write_text(draft.model_dump_json(indent=2), encoding="utf-8")

    assert map_main([str(out), "--confirm"]) == 0
    mappings_dir = tmp_path / "config" / "mappings"
    assert mappings_dir.is_dir()
    persisted = list(mappings_dir.glob("map_*.json"))
    assert len(persisted) == 1
    confirmed = MappingConfig.model_validate_json(persisted[0].read_text(encoding="utf-8"))
    assert confirmed.report.proposed_mappings[0].source_column == "gender"
    assert "Confirmed" in capsys.readouterr().out


def test_map_main_usage(fresh_settings, capsys) -> None:
    from node1.node import map_main

    assert map_main([]) == 2
    assert map_main(["--confirm"]) == 2
    assert map_main(["a.csv", "b.csv"]) == 2
    assert map_main(["a.csv", "--out"]) == 2
    assert "Usage:" in capsys.readouterr().err


def test_map_main_llm_requires_configured_provider(fresh_settings, capsys, tmp_path: Path) -> None:
    from node1.node import map_main

    out = tmp_path / "draft.json"
    assert map_main([str(FIXTURES / "unmapped_export.csv"), "--llm", "--out", str(out)]) == 1
    assert "mapping draft failed" in capsys.readouterr().err
    assert not out.exists()


def test_cli_main_missing_file(fresh_settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(FIXTURES / "does_not_exist.csv")]) == 1
    assert "ERROR" in capsys.readouterr().err


def test_cli_main_usage(fresh_settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "Usage:" in capsys.readouterr().err


def test_cli_main_config_flag_telco_deployment(
    fresh_settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(FIXTURES / "telco_snapshot.csv"), "--config", "telco"]) == 0
    out = capsys.readouterr().out
    assert "status=PASSED" in out
    assert "adapter=mapping:" in out


def test_e2e_iranian_real_shape(fresh_settings) -> None:
    from config.loader import load_node1_config

    output = run_node1(
        FIXTURES / "iranian_snapshot.csv",
        reference_date=REFERENCE_DATE,
        config=load_node1_config("iranian"),
    )
    report = output.validation_report
    assert report.status is ValidationStatus.PASSED
    assert report.adapter_used == "mapping:042b42b28950"
    assert report.matched_candidates == ["mapping:042b42b28950"]
    assert report.n_accepted == 5
    first = output.canonical_dataset[0]
    assert first.customer_id == "0"
    assert first.observation_end == REFERENCE_DATE
    assert first.event_observed == 0
    assert first.core_features.usage_frequency == 71.0
    assert first.meta.original_row_id == "0"
    assert "call_failures" in first.extra_features
    assert "Subscription  Length" not in first.extra_features


def test_cli_main_config_flag_iranian_deployment(
    fresh_settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(FIXTURES / "iranian_snapshot.csv"), "--config", "iranian"]) == 0
    out = capsys.readouterr().out
    assert "status=PASSED" in out
    assert "adapter=mapping:042b42b28950" in out
    assert "matched=mapping:042b42b28950" in out


def test_cli_main_config_flag_missing_value(
    fresh_settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(FIXTURES / "clean_customers.csv"), "--config"]) == 2
    assert "Usage:" in capsys.readouterr().err


def test_cli_main_config_flag_unknown_version(
    fresh_settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(FIXTURES / "clean_customers.csv"), "--config", "nope"]) == 1
    assert "ERROR" in capsys.readouterr().err

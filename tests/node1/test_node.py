from __future__ import annotations

from datetime import UTC, date, datetime
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


def test_unapproved_core_keys_demoted_not_rejected(
    fresh_settings, node1_config: Node1Config
) -> None:
    """A core key missing from approved_core_keys is demoted, not quarantined (§1.8)."""
    subset = Node1Config.model_validate(
        {
            "validation_version": node1_config.validation_version,
            "missingness_threshold": node1_config.missingness_threshold,
            "router_high_confidence_threshold": node1_config.router_high_confidence_threshold,
            "promotion_min_events": node1_config.promotion_min_events,
            "approved_core_keys": ["usage_frequency"],
            "core_key_types": {"usage_frequency": "float"},
            "tenure_sanity": node1_config.tenure_sanity.model_dump(),
        }
    )
    output = run_node1(
        FIXTURES / "clean_customers.csv",
        reference_date=REFERENCE_DATE,
        config=subset,
    )
    report = output.validation_report
    assert report.status is ValidationStatus.PASSED
    assert report.n_accepted == 3
    assert report.n_rejected == 0
    assert report.demoted_features == {"contract_length_months": 3, "plan_tier": 3}
    by_id = {record.customer_id: record for record in output.canonical_dataset}
    assert by_id["cus_1001"].extra_features["plan_tier"] == "pro"
    assert by_id["cus_1001"].extra_features["contract_length_months"] == 12.0
    assert by_id["cus_1002"].extra_features["plan_tier"] == "basic"
    assert by_id["cus_1002"].extra_features["contract_length_months"] == 1.0
    assert by_id["cus_1003"].extra_features["plan_tier"] == "pro"
    assert by_id["cus_1003"].extra_features["contract_length_months"] == 6.0
    assert by_id["cus_1001"].core_features.usage_frequency == 28.4


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


def test_e2e_numeric_string_event_accepted_not_rejected(
    fresh_settings, node1_config: Node1Config, tmp_path: Path
) -> None:
    from adapters.mapping_adapter import load_confirmed_mapping_adapters
    from router.fingerprint import extract_fingerprint
    from router.llm_mapper import confirm_and_persist
    from schemas.mapping import MappingReport

    frame = pd.read_csv(FIXTURES / "unmapped_export.csv")
    frame = frame.copy()
    frame["Status"] = ["1", "0"]  # numeric strings via a string transform
    raw_path = tmp_path / "numeric_status.csv"
    frame.to_csv(raw_path, index=False)

    fingerprint = extract_fingerprint(frame)
    report = MappingReport.model_validate(mapping_payload(fingerprint))
    for mapping in report.proposed_mappings:
        if mapping.target_field == "event_observed":
            mapping.transformation = "str.strip()"
    confirm_and_persist(report, config_dir=tmp_path / "config", confirmed_by="reviewer")
    adapters = load_confirmed_mapping_adapters(tmp_path / "config")

    output = run_node1(
        raw_path,
        reference_date=REFERENCE_DATE,
        config=node1_config,
        adapters=adapters,
    )
    assert output.validation_report.status is ValidationStatus.PASSED
    assert output.validation_report.n_accepted == 2
    assert output.validation_report.n_rejected == 0
    assert {record.event_observed for record in output.canonical_dataset} == {0, 1}


def test_cli_main_success(fresh_settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(FIXTURES / "clean_customers.csv")]) == 0
    assert "status=PASSED" in capsys.readouterr().out


def test_cli_main_unmapped_format(fresh_settings, capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(FIXTURES / "unmapped_export.csv")]) == 1
    err = capsys.readouterr().err
    assert "no deterministic adapter matched" in err
    assert "Onboarding a new dataset" in err


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
    # A confirmable mapping maps every identity field (review L16).
    draft.proposed_mappings = [
        ProposedMapping.model_validate(item)
        for item in mapping_payload(draft.source_fingerprint)["proposed_mappings"]
    ]
    out.write_text(draft.model_dump_json(indent=2), encoding="utf-8")

    assert map_main([str(out), "--confirm"]) == 0
    mappings_dir = tmp_path / "config" / "mappings"
    assert mappings_dir.is_dir()
    persisted = list(mappings_dir.glob("map_*.json"))
    assert len(persisted) == 1
    confirmed = MappingConfig.model_validate_json(persisted[0].read_text(encoding="utf-8"))
    assert confirmed.report.proposed_mappings == draft.proposed_mappings
    assert "Confirmed" in capsys.readouterr().out


@pytest.mark.parametrize(
    "flag_order",
    [
        ["--confirm", "--node1-config", "1"],
        ["--node1-config", "1", "--confirm"],
    ],
)
def test_map_main_confirm_flag_order_independent(
    fresh_settings, monkeypatch, capsys, tmp_path: Path, flag_order: list[str]
) -> None:
    import config.settings as cs
    from node1.node import map_main
    from schemas.mapping import MappingReport, ProposedMapping

    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setattr(cs, "_settings", None)
    out = tmp_path / "draft.json"
    assert map_main([str(FIXTURES / "unmapped_export.csv"), "--out", str(out)]) == 0
    draft = MappingReport.model_validate_json(out.read_text(encoding="utf-8"))
    draft.proposed_mappings = [
        ProposedMapping.model_validate(item)
        for item in mapping_payload(draft.source_fingerprint)["proposed_mappings"]
    ]
    out.write_text(draft.model_dump_json(indent=2), encoding="utf-8")

    assert map_main([str(out), *flag_order]) == 0
    assert "Confirmed" in capsys.readouterr().out
    assert (tmp_path / "config" / "mappings").is_dir()


def test_map_main_usage(fresh_settings, capsys) -> None:
    from node1.node import map_main

    assert map_main([]) == 2
    assert map_main(["--confirm"]) == 2
    assert map_main(["a.csv", "b.csv"]) == 2
    assert map_main(["a.csv", "--out"]) == 2
    assert map_main(["a.csv", "--node1-config", "1", "--confirm", "extra"]) == 2
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


def test_e2e_bank_real_shape(fresh_settings) -> None:
    from config.loader import load_node1_config

    output = run_node1(
        FIXTURES / "bank_snapshot.csv",
        reference_date=REFERENCE_DATE,
        config=load_node1_config("bank"),
    )
    report = output.validation_report
    assert report.status is ValidationStatus.PASSED
    assert report.adapter_used == "mapping:dad50914eb14"
    assert report.matched_candidates == ["mapping:dad50914eb14"]
    assert report.n_accepted == 5
    first = output.canonical_dataset[0]
    assert first.customer_id == "15634602"
    assert first.observation_start == date(2026, 6, 15)
    assert first.observation_end == REFERENCE_DATE
    assert first.event_observed == 1
    assert first.core_features.model_dump() == {k: None for k in first.core_features.model_dump()}
    assert "credit_score" in first.extra_features
    assert "Exited" not in first.extra_features


def test_e2e_cellular_real_shape(fresh_settings) -> None:
    from config.loader import load_node1_config

    output = run_node1(
        FIXTURES / "cellular_snapshot.csv",
        reference_date=REFERENCE_DATE,
        config=load_node1_config("cellular"),
    )
    report = output.validation_report
    assert report.status is ValidationStatus.PASSED
    assert report.adapter_used == "mapping:774dc5f8257a"
    assert report.matched_candidates == ["mapping:774dc5f8257a"]
    assert report.n_accepted == 5
    first = output.canonical_dataset[0]
    assert first.customer_id == "1000002"
    assert first.observation_end == REFERENCE_DATE
    assert first.event_observed == 0
    assert len(first.extra_features) == 75
    assert "MOU" in first.extra_features
    assert "CHURN" not in first.extra_features


def test_e2e_credit_real_shape(fresh_settings) -> None:
    from config.loader import load_node1_config

    output = run_node1(
        FIXTURES / "credit_snapshot.csv",
        reference_date=REFERENCE_DATE,
        config=load_node1_config("credit"),
    )
    report = output.validation_report
    assert report.status is ValidationStatus.PASSED
    assert report.adapter_used == "mapping:dd227148b950"
    assert report.matched_candidates == ["mapping:dd227148b950"]
    assert report.n_accepted == 5
    first = output.canonical_dataset[0]
    assert first.customer_id == "768805383"
    assert first.observation_end == REFERENCE_DATE
    assert first.event_observed == 0
    assert "Customer_Age" in first.extra_features
    assert "Attrition_Flag" not in first.extra_features


def test_cli_main_config_flag_bank_deployment(
    fresh_settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(FIXTURES / "bank_snapshot.csv"), "--config", "bank"]) == 0
    out = capsys.readouterr().out
    assert "status=PASSED" in out
    assert "adapter=mapping:dad50914eb14" in out
    assert "matched=mapping:dad50914eb14" in out


def test_cli_main_config_flag_cellular_deployment(
    fresh_settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(FIXTURES / "cellular_snapshot.csv"), "--config", "cellular"]) == 0
    out = capsys.readouterr().out
    assert "status=PASSED" in out
    assert "adapter=mapping:774dc5f8257a" in out
    assert "matched=mapping:774dc5f8257a" in out


def test_cli_main_config_flag_credit_deployment(
    fresh_settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(FIXTURES / "credit_snapshot.csv"), "--config", "credit"]) == 0
    out = capsys.readouterr().out
    assert "status=PASSED" in out
    assert "adapter=mapping:dd227148b950" in out
    assert "matched=mapping:dd227148b950" in out


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


def test_e2e_canonical_record_contract_shape(fresh_settings, node1_config: Node1Config) -> None:
    stamp = datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC)
    output = run_node1(
        FIXTURES / "clean_customers.csv",
        reference_date=REFERENCE_DATE,
        config=node1_config,
        now=stamp,
    )
    record = output.canonical_dataset[0]
    dumped = record.model_dump(mode="json")

    assert set(dumped) == {
        "customer_id",
        "observation_start",
        "observation_end",
        "event_observed",
        "tenure",
        "core_features",
        "extra_features",
        "meta",
    }
    assert isinstance(dumped["customer_id"], str)
    assert isinstance(dumped["observation_start"], str)
    assert isinstance(dumped["observation_end"], str)
    assert isinstance(dumped["event_observed"], int)
    assert isinstance(dumped["tenure"], float)
    assert isinstance(dumped["core_features"], dict)
    assert isinstance(dumped["extra_features"], dict)
    assert isinstance(dumped["meta"], dict)
    assert set(dumped["meta"]) == {
        "source_adapter",
        "mapping_version",
        "ingested_at",
        "original_row_id",
        "reference_date",
    }

    assert record.observation_start <= record.observation_end
    assert record.tenure == float((record.observation_end - record.observation_start).days)
    assert record.tenure == 521.0
    assert record.event_observed == 0
    assert record.core_features.model_dump() == {
        "plan_tier": "pro",
        "contract_length_months": 12.0,
        "usage_frequency": 28.4,
        "support_tickets_90d": None,
        "contract": None,
        "internet_service": None,
        "monthly_charges": None,
        "senior_citizen": None,
    }
    assert record.extra_features == {"region": "EU", "last_login_days_ago": 3}
    assert record.meta.reference_date.isoformat() == "2026-08-15"


@pytest.mark.parametrize(
    ("file", "config_name"),
    [
        ("telco_snapshot.csv", "telco"),
        ("iranian_snapshot.csv", "iranian"),
        ("bank_snapshot.csv", "bank"),
        ("cellular_snapshot.csv", "cellular"),
        ("credit_snapshot.csv", "credit"),
    ],
)
def test_cli_output_identical_across_runs(
    fresh_settings, capsys: pytest.CaptureFixture[str], file: str, config_name: str
) -> None:
    assert main([str(FIXTURES / file), "--config", config_name]) == 0
    first = capsys.readouterr().out
    assert main([str(FIXTURES / file), "--config", config_name]) == 0
    second = capsys.readouterr().out
    assert first == second


def test_known_answer_clean_and_messy_shapes_match_ground_truth(
    fresh_settings, node1_config: Node1Config, tmp_path: Path
) -> None:
    from adapters.mapping_adapter import load_confirmed_mapping_adapters
    from router.fingerprint import extract_fingerprint
    from router.llm_mapper import confirm_and_persist
    from schemas.mapping import MappingReport

    messy = pd.read_csv(FIXTURES / "known_ground_truth_messy.csv")
    payload = {
        "source_fingerprint": extract_fingerprint(messy).model_dump(mode="json"),
        "proposed_mappings": [
            {
                "source_column": "Cust ID",
                "target_field": "customer_id",
                "confidence": 0.99,
                "transformation": "str.strip()",
            },
            {
                "source_column": "Start Date",
                "target_field": "observation_start",
                "confidence": 0.99,
                "transformation": "parse_date",
            },
            {
                "source_column": "End Date",
                "target_field": "observation_end",
                "confidence": 0.99,
                "transformation": "parse_date",
            },
            {
                "source_column": "Status",
                "target_field": "event_observed",
                "confidence": 0.99,
                "transformation": "map({'Active': 0, 'Churned': 1})",
            },
            {
                "source_column": "Plan",
                "target_field": "core.plan_tier",
                "confidence": 0.99,
                "transformation": "str.strip()",
            },
            {
                "source_column": "Contract Length (Months)",
                "target_field": "core.contract_length_months",
                "confidence": 0.99,
                "transformation": "to_float",
            },
            {
                "source_column": "Usage/Month",
                "target_field": "core.usage_frequency",
                "confidence": 0.99,
                "transformation": "to_float",
            },
        ],
        "unmapped_columns": [],
        "suggested_extra_features": [
            {"source": "Region", "suggested_key": "region"},
            {"source": "Last Login (days)", "suggested_key": "last_login_days_ago"},
        ],
        "data_quality_flags": [],
        "recommended_action": "create_deterministic_adapter",
        "llm_model_used": "test/known-answer",
        "generated_at": "2026-08-17T09:41:12Z",
    }
    report = MappingReport.model_validate(payload)
    confirm_and_persist(report, config_dir=tmp_path / "config", confirmed_by="reviewer")
    adapters = load_confirmed_mapping_adapters(tmp_path / "config")

    stamp = datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC)
    clean = run_node1(
        FIXTURES / "clean_customers.csv",
        reference_date=REFERENCE_DATE,
        config=node1_config,
        now=stamp,
    )
    messy_out = run_node1(
        FIXTURES / "known_ground_truth_messy.csv",
        reference_date=REFERENCE_DATE,
        config=node1_config,
        adapters=adapters,
        now=stamp,
    )

    clean_by_id = {r.customer_id: r for r in clean.canonical_dataset}
    messy_by_id = {r.customer_id: r for r in messy_out.canonical_dataset}
    assert set(clean_by_id) == set(messy_by_id) == {"cus_1001", "cus_1002", "cus_1003"}

    def _project(record) -> dict:
        data = record.model_dump(mode="json")
        data["meta"] = {
            k: v for k, v in data["meta"].items() if k not in {"source_adapter", "mapping_version"}
        }
        return data

    for cid in clean_by_id:
        assert _project(clean_by_id[cid]) == _project(messy_by_id[cid])

    expected = {
        "cus_1001": {
            "start": "2025-03-12",
            "end": "2026-08-15",
            "event": 0,
            "tenure": 521.0,
            "plan": "pro",
            "contract": 12.0,
            "usage": 28.4,
            "region": "EU",
            "last_login": 3,
        },
        "cus_1002": {
            "start": "2024-11-03",
            "end": "2026-02-18",
            "event": 1,
            "tenure": 472.0,
            "plan": "basic",
            "contract": 1.0,
            "usage": 4.1,
            "region": "US",
            "last_login": 41,
        },
        "cus_1003": {
            "start": "2025-01-01",
            "end": "2026-08-15",
            "event": 0,
            "tenure": 591.0,
            "plan": "pro",
            "contract": 6.0,
            "usage": 22.0,
            "region": "EU",
            "last_login": 9,
        },
    }
    for cid, exp in expected.items():
        record = clean_by_id[cid]
        assert record.observation_start.isoformat() == exp["start"]
        assert record.observation_end.isoformat() == exp["end"]
        assert record.event_observed == exp["event"]
        assert record.tenure == exp["tenure"]
        assert record.core_features.plan_tier == exp["plan"]
        assert record.core_features.contract_length_months == exp["contract"]
        assert record.core_features.usage_frequency == exp["usage"]
        assert record.extra_features["region"] == exp["region"]
        assert record.extra_features["last_login_days_ago"] == exp["last_login"]

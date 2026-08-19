from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from config.models import Node1Config, TenureSanityParams
from node1.validation import validate_records
from tests.conftest import make_active_customer

REFERENCE_DATE = date(2026, 8, 15)


def mutate(**changes: Any) -> dict[str, Any]:
    record = make_active_customer()
    for key, value in changes.items():
        if key == "core_features":
            record["core_features"].update(value)
        elif key == "meta":
            record["meta"].update(value)
        else:
            record[key] = value
    return record


def _valid_pair() -> list[dict[str, Any]]:
    from tests.conftest import make_churned_customer

    return [make_active_customer(), make_churned_customer()]


def run(records: list[dict[str, Any]], config: Node1Config):
    return validate_records(records, config=config, reference_date=REFERENCE_DATE)


def test_valid_records_pass_all_gates(node1_config: Node1Config) -> None:
    result = run(_valid_pair(), node1_config)
    assert result.batch_failed is False
    assert len(result.accepted) == 2
    assert result.rejected == []
    assert result.errors == []


def test_gate_required_top_level_keys(node1_config: Node1Config) -> None:
    record = mutate()
    del record["tenure"]
    result = run([record], node1_config)
    assert result.rejected == [record]
    assert any(e["code"] == "REQUIRED_KEYS" for e in result.errors)


def test_gate_required_meta_keys(node1_config: Node1Config) -> None:
    record = mutate()
    del record["meta"]["original_row_id"]
    result = run([record], node1_config)
    assert any(e["code"] == "REQUIRED_META" for e in result.errors)


def test_gate_meta_must_be_a_dict(node1_config: Node1Config) -> None:
    record = mutate()
    record["meta"] = "not-a-dict"
    result = run([record], node1_config)
    assert any(e["code"] == "REQUIRED_META" for e in result.errors)


def test_gate_core_features_must_be_a_dict(node1_config: Node1Config) -> None:
    record = mutate()
    record["core_features"] = "not-a-dict"
    result = run([record], node1_config)
    assert any(e["code"] == "CORE_STRUCTURE" for e in result.errors)


def test_gate_invalid_observation_end(node1_config: Node1Config) -> None:
    record = mutate(observation_end="not-a-date")
    result = run([record], node1_config)
    assert any(e["code"] == "INVALID_DATE" for e in result.errors)


def test_gate_non_string_non_date_values(node1_config: Node1Config) -> None:
    record = mutate(observation_start=12345)
    result = run([record], node1_config)
    assert any(e["code"] == "INVALID_DATE" for e in result.errors)


def test_gate_accepts_date_objects(node1_config: Node1Config) -> None:
    from datetime import date

    record = mutate(observation_start="2025-03-12")
    record["observation_end"] = date(2026, 8, 15)
    result = run([record], node1_config)
    assert not any(e["code"] == "INVALID_DATE" for e in result.errors)


def test_gate_rejects_basic_iso_format(node1_config: Node1Config) -> None:
    record = mutate(observation_start="20260801", observation_end="2026-08-15", tenure=14.0)
    result = run([record], node1_config)
    assert any(e["code"] == "INVALID_DATE" for e in result.errors)
    assert result.accepted == []


def test_gate_rejects_week_date_format(node1_config: Node1Config) -> None:
    record = mutate(observation_start="2026-W33-1", observation_end="2026-08-15", tenure=5.0)
    result = run([record], node1_config)
    assert any(e["code"] == "INVALID_DATE" for e in result.errors)
    assert result.accepted == []


def test_gate_accepts_extended_iso_date(node1_config: Node1Config) -> None:
    record = mutate(observation_start="2026-08-01", observation_end="2026-08-15", tenure=14.0)
    result = run([record], node1_config)
    assert not any(e["code"] == "INVALID_DATE" for e in result.errors)
    assert len(result.accepted) == 1


def test_bad_dates_never_reach_build_report(node1_config: Node1Config) -> None:
    from node1.report import build_report
    from schemas.enums import ValidationStatus

    for start in ("20260801", "2026-W33-1"):
        record = mutate(observation_start=start, observation_end="2026-08-15", tenure=14.0)
        result = run([record], node1_config)
        assert any(e["code"] == "INVALID_DATE" for e in result.errors)
        assert result.accepted == []
        output = build_report(
            [record],
            result,
            adapter_name="clean_csv",
            mapping_version="m",
            reference_date=REFERENCE_DATE,
        )
        assert output.validation_report.status is ValidationStatus.FAILED
        assert output.canonical_dataset == []


def test_gate_customer_id_nonempty(node1_config: Node1Config) -> None:
    record = mutate(customer_id="  ")
    result = run([record], node1_config)
    assert any(e["code"] == "CUSTOMER_ID" for e in result.errors)


def test_gate_unique_customer_id(node1_config: Node1Config) -> None:
    result = run([make_active_customer(), make_active_customer()], node1_config)
    duplicates = [e for e in result.errors if e["code"] == "UNIQUE_ID"]
    assert len(duplicates) == 1
    assert len(result.accepted) == 1


def test_gate_valid_iso_dates(node1_config: Node1Config) -> None:
    record = mutate(observation_start="not-a-date")
    result = run([record], node1_config)
    assert any(e["code"] == "INVALID_DATE" for e in result.errors)


def test_gate_window_order(node1_config: Node1Config) -> None:
    record = mutate(observation_start="2026-08-16", observation_end="2026-08-15", tenure=0.0)
    result = run([record], node1_config)
    assert any(e["code"] == "WINDOW_ORDER" for e in result.errors)


def test_gate_tenure_matches_date_diff(node1_config: Node1Config) -> None:
    record = mutate(tenure=100.0)
    result = run([record], node1_config)
    assert any(e["code"] == "TENURE_MISMATCH" for e in result.errors)


def test_gate_tenure_nonnegative_finite(node1_config: Node1Config) -> None:
    for bad in (-1.0, float("nan"), float("inf"), "nope"):
        record = mutate(tenure=bad)
        result = run([record], node1_config)
        assert any(e["code"] == "TENURE_INVALID" for e in result.errors)


def test_gate_event_observed_binary(node1_config: Node1Config) -> None:
    record = mutate(event_observed=2)
    result = run([record], node1_config)
    assert any(e["code"] == "EVENT_OBSERVED" for e in result.errors)


def test_gate_event_observed_rejects_wrong_types(node1_config: Node1Config) -> None:
    for bad in (1.0, 0.0, True, False, "1", "0", 2):
        record = mutate(event_observed=bad)
        result = run([record], node1_config)
        assert any(e["code"] == "EVENT_OBSERVED" for e in result.errors), bad
        assert result.accepted == []


def test_gate_event_observed_accepts_exact_ints(node1_config: Node1Config) -> None:
    for good in (1, 0):
        record = mutate(event_observed=good)
        result = run([record], node1_config)
        assert not any(e["code"] == "EVENT_OBSERVED" for e in result.errors), good
        assert len(result.accepted) == 1


def test_gate_no_future_leakage(node1_config: Node1Config) -> None:
    record = mutate(observation_end="2026-08-16", event_observed=1)
    result = run([record], node1_config)
    assert any(e["code"] == "FUTURE_LEAKAGE" for e in result.errors)


def test_gate_approved_core_keys(node1_config: Node1Config) -> None:
    record = mutate(core_features={"not_approved": 1})
    result = run([record], node1_config)
    assert any(e["code"] == "CORE_KEYS" for e in result.errors)


def test_gate_core_feature_types(node1_config: Node1Config) -> None:
    bad_types = mutate(core_features={"plan_tier": 5})
    result = run([bad_types], node1_config)
    assert any(e["code"] == "CORE_TYPE" for e in result.errors)

    bad_numeric = mutate(core_features={"usage_frequency": True})
    result = run([bad_numeric], node1_config)
    assert any(e["code"] == "CORE_TYPE" for e in result.errors)


def test_gate_telco_core_feature_types(fresh_settings) -> None:
    from config.loader import load_node1_config

    telco = load_node1_config("telco")
    bad_string = mutate(core_features={"contract": 5, "internet_service": "DSL"})
    result = run([bad_string], telco)
    assert any(e["code"] == "CORE_TYPE" for e in result.errors)

    bad_numeric = mutate(core_features={"monthly_charges": "abc", "senior_citizen": 0.0})
    result = run([bad_numeric], telco)
    assert any(e["code"] == "CORE_TYPE" for e in result.errors)


def test_gate_iranian_core_feature_types(fresh_settings) -> None:
    from config.loader import load_node1_config

    iranian = load_node1_config("iranian")

    def with_usage(value: Any) -> dict[str, Any]:
        record = make_active_customer()
        record["core_features"] = {"usage_frequency": value}
        return record

    result = run([with_usage("71")], iranian)
    assert any(e["code"] == "CORE_TYPE" for e in result.errors)

    result = run([with_usage(71.0)], iranian)
    assert all(e["code"] != "CORE_TYPE" for e in result.errors)


def test_gate_core_feature_missing(node1_config: Node1Config) -> None:
    record = mutate(core_features={"plan_tier": None})
    result = run([record], node1_config)
    assert any(e["code"] == "CORE_MISSING" for e in result.errors)


def test_gate_non_finite_numbers(node1_config: Node1Config) -> None:
    record = mutate(core_features={"usage_frequency": float("inf")})
    result = run([record], node1_config)
    assert any(e["code"] == "CORE_TYPE" for e in result.errors)


def test_gate_column_missingness_rejects_batch(node1_config: Node1Config) -> None:
    records = [
        make_active_customer(),
        mutate(customer_id="x_1", core_features={"contract_length_months": None}),
        mutate(customer_id="x_2", core_features={"contract_length_months": None}),
    ]
    result = run(records, node1_config)
    assert result.batch_failed is True
    assert result.accepted == []
    assert any(e["code"] == "COLUMN_MISSINGNESS" for e in result.errors)


def test_gate_column_missingness_within_threshold_passes(node1_config: Node1Config) -> None:
    records = [
        make_active_customer(),
        mutate(customer_id="x_1", core_features={"contract_length_months": None}),
        make_active_customer(),
        make_active_customer(),
    ]
    records[0]["customer_id"] = "a_0"
    records[2]["customer_id"] = "a_2"
    records[3]["customer_id"] = "a_3"
    result = run(records, node1_config)
    assert result.batch_failed is False  # 1/4 missing = 25% <= 30%


def test_core_value_missing_treats_empty_string_as_missing() -> None:
    from node1.validation import _core_value_missing

    assert _core_value_missing({"core_features": {"plan_tier": ""}}, "plan_tier") is True
    assert _core_value_missing({"core_features": {"plan_tier": None}}, "plan_tier") is True
    assert _core_value_missing({"core_features": {"plan_tier": "pro"}}, "plan_tier") is False
    assert (
        _core_value_missing({"core_features": {"usage_frequency": 0}}, "usage_frequency") is False
    )
    assert _core_value_missing({"core_features": {"plan_tier": ""}}, "usage_frequency") is True


@pytest.mark.parametrize("missing", [None, ""])
def test_gate_column_missingness_boundary_equivalent(
    node1_config: Node1Config, missing: str | None
) -> None:
    reject_records = [
        make_active_customer(),
        mutate(customer_id="x_1", core_features={"plan_tier": missing}),
        mutate(customer_id="x_2", core_features={"plan_tier": missing}),
    ]
    result = run(reject_records, node1_config)
    assert result.batch_failed is True  # 2/3 missing = 67% > 30%
    assert any(e["code"] == "COLUMN_MISSINGNESS" for e in result.errors)

    pass_records = [
        make_active_customer(),
        mutate(customer_id="x_1", core_features={"plan_tier": missing}),
        make_active_customer(),
        make_active_customer(),
    ]
    pass_records[0]["customer_id"] = "a_0"
    pass_records[2]["customer_id"] = "a_2"
    pass_records[3]["customer_id"] = "a_3"
    result = run(pass_records, node1_config)
    assert result.batch_failed is False  # 1/4 missing = 25% <= 30%


def test_gate_tenure_zero_dominated_fails(node1_config: Node1Config) -> None:
    records = [
        mutate(customer_id=f"z_{i}", tenure=0.0, observation_start="2026-08-15") for i in range(4)
    ]
    result = run(records, node1_config)
    assert result.batch_failed is True
    assert any(e["code"] == "TENURE_SANITY" for e in result.errors)


def test_gate_tenure_sanity_accepts_healthy_distribution(node1_config: Node1Config) -> None:
    result = run(_valid_pair(), node1_config)
    assert result.batch_failed is False


def test_gate_tenure_extreme_outliers_fail() -> None:
    config = Node1Config.model_validate(
        {
            "validation_version": "test",
            "approved_core_keys": ["plan_tier", "contract_length_months", "usage_frequency"],
            "tenure_sanity": TenureSanityParams(
                max_zero_fraction=0.8, max_extreme_outlier_ratio=0.10, outlier_std_factor=1.0
            ),
        }
    )
    base = [100.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0]
    records = [
        mutate(
            customer_id=f"o_{i}",
            tenure=t,
            observation_start="2025-01-01",
            observation_end="2026-08-15",
        )
        for i, t in enumerate([*base, 101.0, 102.0])
    ]
    result = run(records, config)
    assert result.batch_failed is True
    assert any(e["code"] == "TENURE_SANITY" for e in result.errors)


def test_empty_input_is_not_accepted(node1_config: Node1Config) -> None:
    result = run([], node1_config)
    assert result.accepted == []

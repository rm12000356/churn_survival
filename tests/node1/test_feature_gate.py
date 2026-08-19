from __future__ import annotations

from typing import Any

from config.models import Node1Config
from node1.feature_gate import (
    _std,
    apply_feature_gate,
    evaluate_promotion,
    feature_gate_records,
    feature_gate_warnings,
)
from tests.conftest import make_active_customer


def _promo_records(feature_values: list[Any], event_labels: list[int]) -> list[dict[str, Any]]:
    records = []
    for index, (value, event) in enumerate(zip(feature_values, event_labels, strict=False)):
        record = make_active_customer()
        record["customer_id"] = f"p_{index}"
        record["extra_features"]["usage_z"] = value
        record["event_observed"] = event
        records.append(record)
    return records


def test_apply_feature_gate_moves_unapproved_keys_to_extra() -> None:
    record = make_active_customer()
    record["core_features"]["sneaky"] = 1
    gated = apply_feature_gate(record, ["plan_tier", "contract_length_months", "usage_frequency"])
    assert "sneaky" not in gated["core_features"]
    assert gated["extra_features"]["sneaky"] == 1


def test_feature_gate_records_counts_demotions_per_key() -> None:
    records = [make_active_customer(), make_active_customer(), make_active_customer()]
    records[1]["customer_id"] = "b_1"
    records[2]["customer_id"] = "b_2"
    gated, demotions = feature_gate_records(records, ["plan_tier"])
    assert demotions == {"contract_length_months": 3, "usage_frequency": 3}
    for record in gated:
        assert set(record["core_features"]) == {"plan_tier"}
        assert "contract_length_months" in record["extra_features"]
        assert "usage_frequency" in record["extra_features"]


def test_feature_gate_records_all_approved_no_demotions() -> None:
    record = make_active_customer()
    gated, demotions = feature_gate_records(
        [record], ["plan_tier", "contract_length_months", "usage_frequency"]
    )
    assert demotions == {}
    assert gated == [record]


def test_feature_gate_records_empty_input() -> None:
    gated, demotions = feature_gate_records([], ["plan_tier"])
    assert gated == []
    assert demotions == {}


def test_wrong_typed_approved_key_still_rejected_after_gate(
    node1_config: Node1Config,
) -> None:
    """Gate runs first, but a wrong-typed APPROVED key must still be quarantined."""
    from datetime import date

    from node1.validation import validate_records

    record = make_active_customer()
    record["core_features"]["plan_tier"] = 5  # approved string key, wrong type
    gated, demotions = feature_gate_records([record], node1_config.approved_core_keys)
    assert demotions == {}  # no demotion — the key IS approved
    result = validate_records(gated, config=node1_config, reference_date=date(2026, 8, 15))
    assert result.accepted == []
    assert result.rejected == [gated[0]]
    assert any(e["code"] == "CORE_TYPE" for e in result.errors)
    assert not any(e["code"] == "CORE_KEYS" for e in result.errors)


def test_promotion_rejected_when_missingness_high(node1_config: Node1Config) -> None:
    records = _promo_records([None, None, None, 1.0], [0, 0, 0, 0])
    verdict = evaluate_promotion("usage_z", records, node1_config)
    assert verdict.recommend_promote is False


def test_promotion_rejected_when_no_variation(node1_config: Node1Config) -> None:
    records = _promo_records([5.0, 5.0, 5.0, 5.0], [0, 0, 0, 0])
    verdict = evaluate_promotion("usage_z", records, node1_config)
    assert verdict.recommend_promote is False


def test_promotion_rejected_with_sparse_events(node1_config: Node1Config) -> None:
    records = _promo_records([1.0, 2.0, 3.0, 4.0], [0, 0, 0, 1])  # 1 event < 30
    verdict = evaluate_promotion("usage_z", records, node1_config, event_labels=[0, 0, 0, 1])
    assert verdict.recommend_promote is False


def test_promotion_requires_explicit_approval(node1_config: Node1Config) -> None:
    records = _promo_records([1.0, 2.0, 3.0, 4.0], [0, 0, 0, 1])
    verdict = evaluate_promotion("usage_z", records, node1_config)
    assert verdict.recommend_promote is False  # no labels, no domain approval


def test_promotion_succeeds_with_domain_approval(node1_config: Node1Config) -> None:
    records = _promo_records([1.0, 2.0, 3.0, 4.0], [0, 0, 0, 0])
    verdict = evaluate_promotion("usage_z", records, node1_config, domain_approved=True)
    assert verdict.recommend_promote is True
    assert any("domain approval" in reason for reason in verdict.reasons)


def test_promotion_warns_on_high_correlation(node1_config: Node1Config) -> None:
    records = []
    for index, usage in enumerate([10.0, 20.0, 30.0, 40.0]):
        record = make_active_customer()
        record["customer_id"] = f"c_{index}"
        record["core_features"]["usage_frequency"] = usage
        record["extra_features"]["usage_z"] = usage  # perfectly correlated
        record["event_observed"] = 0
        records.append(record)
    verdict = evaluate_promotion("usage_z", records, node1_config, domain_approved=True)
    assert verdict.recommend_promote is True
    assert any("high correlation" in warning for warning in verdict.warnings)


def test_feature_gate_warnings_collects_correlation_warnings(node1_config: Node1Config) -> None:
    records = []
    for index, usage in enumerate([10.0, 20.0, 30.0, 40.0]):
        record = make_active_customer()
        record["customer_id"] = f"c_{index}"
        record["core_features"]["usage_frequency"] = usage
        record["extra_features"]["usage_z"] = usage
        record["event_observed"] = 0
        records.append(record)
    warnings = feature_gate_warnings(records, node1_config)
    assert any("usage_z" in warning for warning in warnings)


def test_no_records_no_warnings(node1_config: Node1Config) -> None:
    assert feature_gate_warnings([], node1_config) == []


def test_apply_feature_gate_returns_record_unchanged_when_core_not_dict() -> None:
    record = make_active_customer()
    record["core_features"] = "not-a-dict"
    gated = apply_feature_gate(record, ["plan_tier"])
    assert gated is record


def test_promotion_with_mixed_non_numeric_variation(node1_config: Node1Config) -> None:
    records = _promo_records(["alpha", "beta", "gamma", "delta"], [0, 0, 0, 0])
    verdict = evaluate_promotion("usage_z", records, node1_config, domain_approved=True)
    assert verdict.recommend_promote is True


def test_promotion_with_rich_event_labels_uses_association(node1_config: Node1Config) -> None:
    values = list(range(60))
    events = [1] * 35 + [0] * 25
    records = _promo_records([float(v) for v in values], events)
    verdict = evaluate_promotion("usage_z", records, node1_config, event_labels=events)
    assert verdict.recommend_promote is True
    assert any("statistical association" in reason for reason in verdict.reasons)


def test_promotion_association_handles_all_absent_values(node1_config: Node1Config) -> None:
    values = [0, False, "", 0, False, ""] * 10  # unique values but all "absent" per _association
    events = [1] * 30 + [0] * 30
    records = _promo_records(values, events)
    verdict = evaluate_promotion("usage_z", records, node1_config, event_labels=events)
    assert verdict.recommend_promote is True


def test_std_empty_is_zero() -> None:
    assert _std([]) == 0.0

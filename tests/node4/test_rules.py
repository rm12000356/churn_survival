"""Tasks 5.7 / 5.8 — significant flags, critical rules, level, insufficient (§4.8–§4.13)."""

from __future__ import annotations

from config.loader import load_node4_config
from node4.rules import (
    classify_risk_level,
    evaluate_critical_rules,
    is_insufficient_data,
    is_significant,
)
from schemas.enums import ReasonType, RiskLevel, SupportDataStatus
from tests.node4.conftest import make_flag


def test_positive_feedback_never_significant() -> None:
    flag = make_flag("positive_feedback", severity="high", signal_strength="strong")
    assert is_significant(flag) is False


def test_significant_predicate() -> None:
    assert is_significant(make_flag("other", severity="medium", signal_strength="weak")) is True
    assert is_significant(make_flag("other", severity="low", signal_strength="strong")) is True
    assert is_significant(make_flag("other", severity="low", signal_strength="weak")) is False


def test_rule_1_moderate_or_strong_cancellation() -> None:
    config = load_node4_config("1")
    positive = [make_flag("cancellation_intent", severity="medium", signal_strength="moderate")]
    assert ReasonType.CRITICAL_CANCELLATION_INTENT in evaluate_critical_rules(0.0, positive, config)


def test_rule_1_weak_cancellation_alone_is_not_critical() -> None:
    config = load_node4_config("1")
    weak = [make_flag("cancellation_intent", severity="low", signal_strength="weak")]
    assert evaluate_critical_rules(0.0, weak, config) == []


def test_rule_2_strong_cancellation_plus_another_significant_flag() -> None:
    config = load_node4_config("1")
    flags = [
        make_flag("cancellation_intent", severity="high", signal_strength="strong"),
        make_flag("billing_complaint", severity="high", signal_strength="strong"),
    ]
    rules = evaluate_critical_rules(0.0, flags, config)
    assert ReasonType.CRITICAL_CANCELLATION_INTENT in rules
    assert ReasonType.CRITICAL_CANCELLATION_PLUS_SIGNIFICANT_FLAG in rules


def test_rule_2_requires_a_different_flag_type() -> None:
    config = load_node4_config("1")
    flags = [
        make_flag("cancellation_intent", severity="high", signal_strength="strong"),
        make_flag(
            "cancellation_intent",
            severity="high",
            signal_strength="strong",
            message_id="m2",
        ),
    ]
    rules = evaluate_critical_rules(0.0, flags, config)
    assert ReasonType.CRITICAL_CANCELLATION_PLUS_SIGNIFICANT_FLAG not in rules


def test_rule_2_ignores_weak_other_flags() -> None:
    config = load_node4_config("1")
    flags = [
        make_flag("cancellation_intent", severity="high", signal_strength="strong"),
        make_flag("billing_complaint", severity="low", signal_strength="weak"),
    ]
    rules = evaluate_critical_rules(0.0, flags, config)
    assert ReasonType.CRITICAL_CANCELLATION_PLUS_SIGNIFICANT_FLAG not in rules


def test_rule_3_high_quant_plus_strong_contract_concern() -> None:
    config = load_node4_config("1")
    flags = [
        make_flag("renewal_or_contract_concern", severity="high", signal_strength="strong")
    ]
    assert ReasonType.CRITICAL_HIGH_QUANT_PLUS_CONTRACT_CONCERN in evaluate_critical_rules(
        0.7, flags, config
    )
    assert ReasonType.CRITICAL_HIGH_QUANT_PLUS_CONTRACT_CONCERN not in evaluate_critical_rules(
        0.69, flags, config
    )


def test_rule_4_repeated_high_severity_plus_high_quant() -> None:
    config = load_node4_config("1")
    flags = [
        make_flag(
            "product_bug_or_outage",
            severity="high",
            signal_strength="moderate",
            recurrence_count=2,
        )
    ]
    assert ReasonType.CRITICAL_REPEATED_HIGH_SEVERITY_PLUS_HIGH_QUANT in evaluate_critical_rules(
        0.7, flags, config
    )
    single = [
        make_flag(
            "product_bug_or_outage",
            severity="high",
            signal_strength="moderate",
            recurrence_count=1,
        )
    ]
    assert ReasonType.CRITICAL_REPEATED_HIGH_SEVERITY_PLUS_HIGH_QUANT not in (
        evaluate_critical_rules(0.7, single, config)
    )


def test_critical_rules_need_quantitative_for_rules_3_and_4() -> None:
    config = load_node4_config("1")
    flags = [make_flag("renewal_or_contract_concern", severity="high", signal_strength="strong")]
    assert evaluate_critical_rules(None, flags, config) == []


def test_classify_risk_level_thresholds() -> None:
    config = load_node4_config("1")
    assert classify_risk_level(0.9, [], config) == RiskLevel.HIGH
    assert classify_risk_level(0.7, [], config) == RiskLevel.HIGH
    assert classify_risk_level(0.699, [], config) == RiskLevel.MEDIUM
    assert classify_risk_level(0.4, [], config) == RiskLevel.MEDIUM
    assert classify_risk_level(0.399, [], config) == RiskLevel.LOW


def test_classify_risk_level_critical_first() -> None:
    config = load_node4_config("1")
    rules = [ReasonType.CRITICAL_CANCELLATION_INTENT]
    assert classify_risk_level(0.0, rules, config) == RiskLevel.CRITICAL


def test_insufficient_data_predicate() -> None:
    assert is_insufficient_data(None, SupportDataStatus.NO_DATA) is True
    assert is_insufficient_data(None, SupportDataStatus.LIMITED_DATA) is False
    assert is_insufficient_data(0.0, SupportDataStatus.NO_DATA) is False
    assert is_insufficient_data(0.9, SupportDataStatus.SUFFICIENT_DATA) is False

"""Tasks 5.8 / 5.9 — deterministic primary reasons and conflict recording (§4.11/§4.16/§4.17)."""

from __future__ import annotations

from config.loader import load_node4_config
from node4.node import run_node4
from node4.reasons import format_reason
from schemas.enums import ReasonType
from schemas.node4 import StructuredReason
from tests.node4.conftest import make_flag, make_node2, make_node3, make_signal

_CRITICAL_TYPES = {
    ReasonType.CRITICAL_CANCELLATION_INTENT,
    ReasonType.CRITICAL_CANCELLATION_PLUS_SIGNIFICANT_FLAG,
    ReasonType.CRITICAL_HIGH_QUANT_PLUS_CONTRACT_CONCERN,
    ReasonType.CRITICAL_REPEATED_HIGH_SEVERITY_PLUS_HIGH_QUANT,
}


def _account(node2, node3):
    config = load_node4_config("1")
    output = run_node4(node2, node3, config)
    return (output.ranked_accounts + output.insufficient_data_accounts)[0]


def _types(account) -> set[str]:
    return {reason.reason_type.value for reason in account.primary_reasons}


def _signal(customer_id: str, flag) -> object:
    return make_signal(customer_id, risk_flags=[flag])


def test_rule_1_reason_recorded() -> None:
    flag = make_flag(
        "cancellation_intent", signal_strength="moderate", severity="medium"
    )
    account = _account(
        make_node2(["A"], risk_scores=[0.0], survival_90=[1.0]),
        make_node3([_signal("A", flag)]),
    )
    assert "critical_cancellation_intent" in _types(account)


def test_rule_2_reason_recorded() -> None:
    account = _account(
        make_node2(["A"], risk_scores=[0.0], survival_90=[1.0]),
        make_node3(
            [
                make_signal(
                    "A",
                    risk_flags=[
                        make_flag("cancellation_intent", signal_strength="strong"),
                        make_flag("billing_complaint", signal_strength="strong"),
                    ],
                )
            ]
        ),
    )
    assert "critical_cancellation_plus_significant_flag" in _types(account)


def test_rule_3_reason_recorded() -> None:
    flag = make_flag("renewal_or_contract_concern", signal_strength="strong")
    account = _account(
        make_node2(["A"], risk_scores=[0.9], survival_90=[0.1]),
        make_node3([_signal("A", flag)]),
    )
    assert "critical_high_quant_plus_contract_concern" in _types(account)


def test_rule_4_reason_recorded() -> None:
    flag = make_flag(
        "product_bug_or_outage",
        severity="high",
        signal_strength="moderate",
        recurrence_count=2,
    )
    account = _account(
        make_node2(["A"], risk_scores=[0.9], survival_90=[0.1]),
        make_node3([_signal("A", flag)]),
    )
    assert "critical_repeated_high_severity_plus_high_quant" in _types(account)


def test_every_critical_customer_has_a_qualifying_reason() -> None:
    config = load_node4_config("1")
    node2 = make_node2(["A"], risk_scores=[0.9], survival_90=[0.1])
    node3 = make_node3(
        [make_signal("A", risk_flags=[make_flag("cancellation_intent", signal_strength="strong")])]
    )
    output = run_node4(node2, node3, config)
    for account in output.ranked_accounts:
        if account.combined_risk_level.value == "critical":
            assert any(
                reason.reason_type in _CRITICAL_TYPES for reason in account.primary_reasons
            )


def test_quantitative_reasons() -> None:
    high = _account(
        make_node2(["A"], risk_scores=[0.9], survival_90=[0.1]), make_node3([])
    )
    assert "high_quantitative_risk" in _types(high)
    moderate = _account(
        make_node2(["A"], risk_scores=[0.5], survival_90=[0.5]), make_node3([])
    )
    assert "moderate_quantitative_risk" in _types(moderate)


def test_missing_data_reasons() -> None:
    account = _account(None, make_node3([make_signal("A", support_data_status="no_data")]))
    assert "missing_quantitative_data" in _types(account)
    assert "missing_support_data" in _types(account)


def test_limited_support_data_reason() -> None:
    account = _account(
        make_node2(["A"], risk_scores=[0.5], survival_90=[0.5]),
        make_node3([make_signal("A", support_data_status="limited_data")]),
    )
    assert "limited_support_data" in _types(account)


def test_support_signal_and_urgency_reasons() -> None:
    strong = _account(
        make_node2(["A"], risk_scores=[0.1], survival_90=[0.9]),
        make_node3(
            [
                make_signal(
                    "A",
                    risk_flags=[make_flag("billing_complaint", signal_strength="strong")],
                )
            ]
        ),
    )
    assert "strong_support_signal" in _types(strong)

    moderate = _account(
        make_node2(["A"], risk_scores=[0.1], survival_90=[0.9]),
        make_node3(
            [
                make_signal(
                    "A",
                    risk_flags=[make_flag("billing_complaint", signal_strength="moderate")],
                )
            ]
        ),
    )
    assert "moderate_support_signal" in _types(moderate)

    urgent = _account(
        make_node2(["A"], risk_scores=[0.1], survival_90=[0.9]),
        make_node3([make_signal("A", urgency_level="high")]),
    )
    assert "high_support_urgency" in _types(urgent)


def test_repeated_support_issue_reason() -> None:
    account = _account(
        make_node2(["A"], risk_scores=[0.1], survival_90=[0.9]),
        make_node3(
            [make_signal("A", risk_flags=[make_flag("billing_complaint", recurrence_count=3)])]
        ),
    )
    assert "repeated_support_issue" in _types(account)


def test_conflict_high_quant_plus_positive_support() -> None:
    account = _account(
        make_node2(["A"], risk_scores=[0.9], survival_90=[0.1]),
        make_node3(
            [
                make_signal(
                    "A",
                    risk_flags=[make_flag("positive_feedback", signal_strength="strong")],
                )
            ]
        ),
    )
    assert "quantitative_qualitative_conflict" in _types(account)
    assert "quantitative_qualitative_agreement" not in _types(account)


def test_conflict_low_quant_plus_strong_cancellation() -> None:
    account = _account(
        make_node2(["A"], risk_scores=[0.05], survival_90=[0.95]),
        make_node3(
            [
                make_signal(
                    "A",
                    risk_flags=[make_flag("cancellation_intent", signal_strength="strong")],
                )
            ]
        ),
    )
    assert "quantitative_qualitative_conflict" in _types(account)
    assert account.combined_risk_level.value == "critical"


def test_conflict_high_quant_plus_no_support_data() -> None:
    account = _account(
        make_node2(["A"], risk_scores=[0.9], survival_90=[0.1]), make_node3([])
    )
    assert "quantitative_qualitative_conflict" in _types(account)


def test_agreement_high_quant_plus_strong_signal() -> None:
    account = _account(
        make_node2(["A"], risk_scores=[0.9], survival_90=[0.1]),
        make_node3(
            [
                make_signal(
                    "A",
                    risk_flags=[make_flag("billing_complaint", signal_strength="strong")],
                )
            ]
        ),
    )
    assert "quantitative_qualitative_agreement" in _types(account)


def test_reason_count_limit_never_caps_critical() -> None:
    account = _account(
        make_node2(["A"], risk_scores=[0.9], survival_90=[0.1]),
        make_node3(
            [
                make_signal(
                    "A",
                    risk_flags=[
                        make_flag("cancellation_intent", signal_strength="strong"),
                        make_flag("renewal_or_contract_concern", signal_strength="strong"),
                        make_flag("product_bug_or_outage", severity="high", recurrence_count=2),
                    ],
                )
            ]
        ),
    )
    assert any(reason.reason_type in _CRITICAL_TYPES for reason in account.primary_reasons)


def test_format_reason_is_deterministic() -> None:
    reason = StructuredReason(
        reason_type=ReasonType.HIGH_QUANTITATIVE_RISK,
        source="node2",
        severity="high",
        evidence_ref={"normalized_risk": 0.9},
    )
    first = format_reason(reason)
    assert first == format_reason(reason)
    assert "quantitative" in first
    assert "node2" in first


# --------------------------------------------------------------------------- #
# F-2 — positive feedback must not create or promote risk (architecture §4.29)
# --------------------------------------------------------------------------- #
def test_praise_only_matches_missing_support_baseline() -> None:
    node2 = make_node2(["A"], risk_scores=[0.8], survival_90=[0.2])
    baseline = _account(node2, make_node3([]))
    with_praise = _account(
        node2,
        make_node3(
            [
                make_signal(
                    "A",
                    risk_flags=[make_flag("positive_feedback", signal_strength="strong")],
                )
            ]
        ),
    )
    assert with_praise.combined_score == baseline.combined_score
    assert with_praise.combined_risk_level == baseline.combined_risk_level
    assert "strong_support_signal" not in _types(with_praise)
    assert "quantitative_qualitative_agreement" not in _types(with_praise)


def test_positive_recurrence_only_never_creates_risk() -> None:
    account = _account(
        make_node2(["A"], risk_scores=[0.0], survival_90=[1.0]),
        make_node3(
            [
                make_signal(
                    "A",
                    risk_flags=[
                        make_flag(
                            "positive_feedback",
                            signal_strength="strong",
                            recurrence_count=10,
                        )
                    ],
                )
            ]
        ),
    )
    assert account.combined_score == 0.0
    assert account.combined_risk_level.value == "low"
    assert "repeated_support_issue" not in _types(account)
    assert "strong_support_signal" not in _types(account)


def test_positive_feedback_does_not_promote_medium_to_high() -> None:
    # Case C: renewal concern (moderate) + positive feedback (strong), quant 0.75.
    account = _account(
        make_node2(["A"], risk_scores=[0.75], survival_90=[0.25]),
        make_node3(
            [
                make_signal(
                    "A",
                    risk_flags=[
                        make_flag(
                            "renewal_or_contract_concern",
                            severity="medium",
                            signal_strength="moderate",
                        ),
                        make_flag(
                            "positive_feedback",
                            severity="high",
                            signal_strength="strong",
                        ),
                    ],
                )
            ]
        ),
    )
    assert account.combined_risk_level.value == "medium"
    assert "strong_support_signal" not in _types(account)
    assert "quantitative_qualitative_agreement" not in _types(account)


def test_risk_flag_selected_over_positive_for_reasons() -> None:
    # Case D: cancellation (moderate) + positive feedback (strong).
    account = _account(
        make_node2(["A"], risk_scores=[0.1], survival_90=[0.9]),
        make_node3(
            [
                make_signal(
                    "A",
                    risk_flags=[
                        make_flag("positive_feedback", signal_strength="strong"),
                        make_flag(
                            "cancellation_intent",
                            severity="medium",
                            signal_strength="moderate",
                        ),
                    ],
                )
            ]
        ),
    )
    assert account.combined_risk_level.value == "critical"
    assert "critical_cancellation_intent" in _types(account)
    assert "strong_support_signal" not in _types(account)


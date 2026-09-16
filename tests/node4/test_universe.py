"""Tasks 5.2 / I-15 / I-16 / I-17 — universe, duplicates, partial alignment (§4.3, D-6/D-7)."""

from __future__ import annotations

from config.loader import load_node4_config
from node4.node import run_node4
from tests.node4.conftest import make_flag, make_node2, make_node3, make_signal


def _all_accounts(output):
    return {a.customer_id: a for a in output.ranked_accounts} | {
        a.customer_id: a for a in output.insufficient_data_accounts
    }


def test_union_matrix_yes_yes() -> None:
    config = load_node4_config("1")
    node2 = make_node2(["A"], risk_scores=[0.5], survival_90=[0.5])
    node3 = make_node3([make_signal("A", risk_flags=[make_flag("billing_complaint")])])
    output = run_node4(node2, node3, config)
    accounts = _all_accounts(output)
    assert list(accounts) == ["A"]
    assert output.summary_stats.n_customers == 1
    assert accounts["A"].qualitative.support_data_status.value == "sufficient_data"


def test_union_matrix_yes_no_is_quantitative_only() -> None:
    config = load_node4_config("1")
    node2 = make_node2(["A"], risk_scores=[0.5], survival_90=[0.5])
    output = run_node4(node2, make_node3([]), config)
    account = _all_accounts(output)["A"]
    assert account.quantitative.normalized_risk == 0.5
    assert account.qualitative.support_data_status.value == "no_data"
    assert account.evidence_refs.node3.signal_version == ""
    assert account.evidence_refs.node3.thread_ids == []


def test_union_matrix_no_yes_is_qualitative_only() -> None:
    config = load_node4_config("1")
    node3 = make_node3(
        [
            make_signal(
                "A",
                risk_flags=[make_flag("cancellation_intent", signal_strength="strong")],
            )
        ]
    )
    output = run_node4(None, node3, config)
    account = _all_accounts(output)["A"]
    assert account.quantitative.normalized_risk is None
    assert account.combined_risk_level.value == "critical"
    assert account.evidence_refs.node2.model_version == ""


def test_union_matrix_no_no_is_impossible() -> None:
    config = load_node4_config("1")
    output = run_node4(make_node2([]), make_node3([]), config)
    assert output.summary_stats.n_customers == 0
    assert output.ranked_accounts == []
    assert output.insufficient_data_accounts == []


def test_duplicate_node2_keeps_first_and_records_error() -> None:
    config = load_node4_config("1")
    node2 = make_node2(
        ["A", "A", "B"],
        states=["scored", "scored", "scored"],
        risk_scores=[0.1, 0.9, 0.2],
        survival_90=[0.9, 0.1, 0.8],
    )
    output = run_node4(node2, make_node3([]), config)
    accounts = _all_accounts(output)
    assert set(accounts) == {"A", "B"}
    assert accounts["A"].quantitative.normalized_risk == 0.1  # first occurrence wins
    duplicates = [e for e in output.processing_report.errors if e["code"] == "DUPLICATE_CUSTOMER"]
    assert len(duplicates) == 1
    assert duplicates[0]["customer_id"] == "A"
    assert duplicates[0]["source"] == "node2"


def test_duplicate_node3_keeps_first_and_records_error() -> None:
    config = load_node4_config("1")
    node3 = make_node3(
        [
            make_signal("A", risk_flags=[make_flag("billing_complaint")]),
            make_signal("A", risk_flags=[make_flag("cancellation_intent")]),
        ]
    )
    output = run_node4(make_node2([]), node3, config)
    accounts = _all_accounts(output)
    assert list(accounts) == ["A"]
    assert accounts["A"].qualitative.top_flags[0]["flag_type"] == "billing_complaint"
    duplicates = [e for e in output.processing_report.errors if e["code"] == "DUPLICATE_CUSTOMER"]
    assert len(duplicates) == 1
    assert duplicates[0]["source"] == "node3"


def test_partial_alignment_demotes_to_qualitative_only() -> None:
    config = load_node4_config("1")
    # scored, but no risk_scores and no 90d values -> unusable quantitative entry
    node2 = make_node2(
        ["A"],
        states=["scored"],
        risk_scores=None,
        survival_90=None,
        horizon_status="INSUFFICIENT_DATA",
    )
    node3 = make_node3([make_signal("A", risk_flags=[make_flag("billing_complaint")])])
    output = run_node4(node2, node3, config)
    account = _all_accounts(output)["A"]
    assert account.quantitative.normalized_risk is None
    assert account.combined_confidence == 0.36
    assert any(e["code"] == "PARTIAL_ALIGNMENT" for e in output.processing_report.errors)
    assert any(
        r.reason_type.value == "missing_quantitative_data" for r in account.primary_reasons
    )
    # qualitative synthesis still performed
    assert account.qualitative.support_data_status.value == "sufficient_data"


# --------------------------------------------------------------------------- #
# F-6 — `no_data` + risk flags is inconsistent upstream input (§3.8.6 / §4.26)
# --------------------------------------------------------------------------- #
def test_no_data_with_risk_flags_is_treated_as_inconsistent() -> None:
    config = load_node4_config("1")
    node3 = make_node3(
        [
            make_signal(
                "A",
                support_data_status="no_data",
                risk_flags=[make_flag("cancellation_intent", signal_strength="strong")],
                overall_signal_confidence=0.8,
                churn_language_detected=True,
                escalation_signal=True,
                urgency_level="high",
            )
        ]
    )
    output = run_node4(None, node3, config)
    account = _all_accounts(output)["A"]
    # Status is authoritative: the customer is insufficient, not Critical.
    assert account.combined_risk_level.value == "insufficient_data"
    assert account.rank is None
    types = [r.reason_type.value for r in account.primary_reasons]
    assert types == ["missing_quantitative_data", "missing_support_data"]
    assert not any(t.startswith("critical_") for t in types)
    # No support-derived field may leak from a no_data record.
    assert account.qualitative.overall_signal_confidence == 0.0
    assert account.qualitative.churn_language_detected is False
    assert account.qualitative.escalation_signal is False
    assert account.qualitative.signal_strength.value == "none"
    assert account.qualitative.top_flags == []
    assert "high_support_urgency" not in types
    assert any(
        e["code"] == "INCONSISTENT_SUPPORT_STATUS" for e in output.processing_report.errors
    )


def test_no_data_with_risk_flags_ignores_flags_when_quant_present() -> None:
    config = load_node4_config("1")
    node2 = make_node2(["A"], risk_scores=[0.5], survival_90=[0.5])
    node3 = make_node3(
        [
            make_signal(
                "A",
                support_data_status="no_data",
                risk_flags=[make_flag("cancellation_intent", signal_strength="strong")],
            )
        ]
    )
    output = run_node4(node2, node3, config)
    account = _all_accounts(output)["A"]
    # quantitative present -> main list; the spurious flag must not make it Critical
    assert account.combined_risk_level.value == "low"
    assert not any(
        r.reason_type.value.startswith("critical_") for r in account.primary_reasons
    )
    assert account.qualitative.signal_strength.value == "none"
    assert any(
        e["code"] == "INCONSISTENT_SUPPORT_STATUS" for e in output.processing_report.errors
    )

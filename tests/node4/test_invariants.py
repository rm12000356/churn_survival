"""Task 5.12 — the §4.27 required invariants plus I-14…I-17 (D-1…D-8)."""

from __future__ import annotations

from config.loader import load_node4_config
from node4.node import _build_alignment, run_node4
from tests.node4.conftest import make_flag, make_node2, make_node3, make_signal


def _accounts(output):
    return {a.customer_id: a for a in output.ranked_accounts} | {
        a.customer_id: a for a in output.insufficient_data_accounts
    }


def _sample_inputs():
    node2 = make_node2(
        ["A", "B", "C"],
        states=["scored", "scored", "scored"],
        risk_scores=[0.9, 0.4, 0.1],
        survival_90=[0.1, 0.6, 0.9],
        feature_associations=[],
    )
    node3 = make_node3(
        [
            make_signal(
                "A",
                risk_flags=[make_flag("cancellation_intent", signal_strength="strong")],
                churn_language_detected=True,
            ),
            make_signal(
                "B",
                risk_flags=[make_flag("billing_complaint", signal_strength="moderate")],
            ),
        ]
    )
    return node2, node3


def test_determinism_identical_output() -> None:
    config = load_node4_config("1")
    node2, node3 = _sample_inputs()
    first = run_node4(node2, node3, config)
    second = run_node4(node2, node3, config)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_customer_universe() -> None:
    config = load_node4_config("1")
    node2, node3 = _sample_inputs()
    output = run_node4(node2, node3, config)
    accounts = _accounts(output)
    assert set(accounts) == {"A", "B", "C"}


def test_rank_stability() -> None:
    config = load_node4_config("1")
    node2, node3 = _sample_inputs()
    before = run_node4(node2, node3, config)
    node2_extra = make_node2(
        ["A", "B", "C", "D"],
        states=["scored"] * 4,
        risk_scores=[0.9, 0.4, 0.1, 0.05],
        survival_90=[0.1, 0.6, 0.9, 0.95],
    )
    after = run_node4(node2_extra, node3, config)
    before_by_id = {a.customer_id: a for a in before.ranked_accounts}
    after_by_id = {a.customer_id: a for a in after.ranked_accounts}
    before_order = [a.customer_id for a in before.ranked_accounts]
    after_order = [a.customer_id for a in after.ranked_accounts if a.customer_id != "D"]
    assert before_order == after_order
    for customer_id in before_order:
        assert before_by_id[customer_id].combined_score == after_by_id[customer_id].combined_score


def test_explanation_independence() -> None:
    config = load_node4_config("1")
    node2, node3 = _sample_inputs()
    output = run_node4(node2, node3, config)
    assert all(a.explanation is None for a in output.ranked_accounts)
    assert all(a.explanation is None for a in output.insufficient_data_accounts)


def test_llm_failure_does_not_prevent_result() -> None:
    # Node 4 v1 has no LLM dependency at all (D-5); the deterministic run is the
    # sole acceptance path.
    config = load_node4_config("1")
    node2, node3 = _sample_inputs()
    assert run_node4(node2, node3, config).ranked_accounts


def test_node3_failure_quantitative_only() -> None:
    config = load_node4_config("1")
    node2 = make_node2(["A"], risk_scores=[0.9], survival_90=[0.1])
    output = run_node4(node2, None, config)
    assert output.summary_stats.n_customers == 1
    assert output.ranked_accounts[0].quantitative.normalized_risk == 0.9


def test_node2_failure_strong_qualitative_still_critical() -> None:
    config = load_node4_config("1")
    node3 = make_node3(
        [make_signal("A", risk_flags=[make_flag("cancellation_intent", signal_strength="strong")])]
    )
    output = run_node4(None, node3, config)
    assert output.ranked_accounts[0].combined_risk_level.value == "critical"


def test_critical_rule_requirement() -> None:
    config = load_node4_config("1")
    node2, node3 = _sample_inputs()
    output = run_node4(node2, node3, config)
    for account in output.ranked_accounts:
        if account.combined_risk_level.value == "critical":
            assert any(
                reason.reason_type.value.startswith("critical_")
                for reason in account.primary_reasons
            )


def test_critical_score_protection() -> None:
    config = load_node4_config("1")
    # Highest possible combined score with no explicit critical rule.
    node2 = make_node2(["A"], risk_scores=[1.0], survival_90=[0.0])
    node3 = make_node3(
        [make_signal("A", risk_flags=[make_flag("billing_complaint", signal_strength="strong")])]
    )
    output = run_node4(node2, node3, config)
    account = output.ranked_accounts[0]
    assert account.combined_score >= 0.7
    assert account.combined_risk_level.value == "high"


def test_positive_feedback_protection() -> None:
    config = load_node4_config("1")
    baseline = run_node4(
        make_node2(["A"], risk_scores=[0.0], survival_90=[1.0]),
        make_node3(
            [
                make_signal(
                    "A",
                    risk_flags=[make_flag("billing_complaint", signal_strength="strong")],
                )
            ]
        ),
        config,
    )
    with_positive = run_node4(
        make_node2(["A"], risk_scores=[0.0], survival_90=[1.0]),
        make_node3(
            [
                make_signal(
                    "A",
                    risk_flags=[
                        make_flag("billing_complaint", signal_strength="strong"),
                        make_flag("positive_feedback", signal_strength="strong"),
                    ],
                )
            ]
        ),
        config,
    )
    assert (
        baseline.ranked_accounts[0].combined_score
        == with_positive.ranked_accounts[0].combined_score
    )


def test_no_data_protection() -> None:
    config = load_node4_config("1")
    node2 = make_node2(["A"], risk_scores=[0.5], survival_90=[0.5])
    output = run_node4(node2, make_node3([]), config)
    account = output.ranked_accounts[0]
    assert any(
        reason.reason_type.value == "missing_support_data" for reason in account.primary_reasons
    )
    # absence of support data never becomes positive evidence
    assert account.qualitative.overall_signal_confidence == 0.0


def test_evidence_traceability_for_critical() -> None:
    config = load_node4_config("1")
    node2, node3 = _sample_inputs()
    output = run_node4(node2, node3, config)
    critical = [a for a in output.ranked_accounts if a.combined_risk_level.value == "critical"]
    assert critical
    for account in critical:
        assert account.evidence_refs.node2.model_version
        assert account.evidence_refs.node3.signal_version
        assert account.evidence_refs.node3.message_ids


def test_rank_independence_from_explanation() -> None:
    config = load_node4_config("1")
    node2, node3 = _sample_inputs()
    output = run_node4(node2, node3, config)
    assert [a.rank for a in output.ranked_accounts] == list(
        range(1, len(output.ranked_accounts) + 1)
    )


def test_i14_scored_subset_alignment() -> None:
    node2 = make_node2(
        ["C1", "C2", "C3", "C4", "C5"],
        states=["scored", "not_enough_data", "excluded", "scored", "scored"],
        risk_scores=[0.10, 0.20, 0.30],
        survival_90=[0.90, 0.80, 0.70],
    )
    alignment = _build_alignment(node2)
    assert alignment.scored_position == {"C1": 0, "C4": 1, "C5": 2}
    assert "C2" not in alignment.scored_position
    assert "C3" not in alignment.scored_position


def test_i15_union_matrix() -> None:
    config = load_node4_config("1")
    both = run_node4(
        make_node2(["A"], risk_scores=[0.5], survival_90=[0.5]),
        make_node3([make_signal("A")]),
        config,
    )
    assert both.summary_stats.n_customers == 1
    quant_only = run_node4(
        make_node2(["B"], risk_scores=[0.5], survival_90=[0.5]), make_node3([]), config
    )
    assert _accounts(quant_only)["B"].qualitative.support_data_status.value == "no_data"
    qual_only = run_node4(None, make_node3([make_signal("C")]), config)
    assert _accounts(qual_only)["C"].quantitative.normalized_risk is None


def test_i16_duplicate_retention() -> None:
    config = load_node4_config("1")
    node2 = make_node2(
        ["A", "A"],
        states=["scored", "scored"],
        risk_scores=[0.1, 0.9],
        survival_90=[0.9, 0.1],
    )
    output = run_node4(node2, make_node3([]), config)
    assert list(_accounts(output)) == ["A"]
    assert _accounts(output)["A"].quantitative.normalized_risk == 0.1
    assert any(e["code"] == "DUPLICATE_CUSTOMER" for e in output.processing_report.errors)


def test_i17_partial_alignment_demotion() -> None:
    config = load_node4_config("1")
    node2 = make_node2(
        ["A"], states=["scored"], risk_scores=[], survival_90=[], horizon_status="INSUFFICIENT_DATA"
    )
    node3 = make_node3([make_signal("A", risk_flags=[make_flag("billing_complaint")])])
    output = run_node4(node2, node3, config)
    account = _accounts(output)["A"]
    assert account.quantitative.normalized_risk is None
    assert account.combined_confidence == 0.36
    assert any(e["code"] == "PARTIAL_ALIGNMENT" for e in output.processing_report.errors)

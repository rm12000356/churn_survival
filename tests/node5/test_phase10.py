"""Node 5 presentation of phase-10 fields (forward risk, lift, churned, conf_v2).

Node 5 copies the new Node 4 fields verbatim, lists churned customers separately
(never ranked), and refuses a Node 4 output whose three lists overlap.
"""

from __future__ import annotations

import pytest

from config.loader import load_node4_config
from config.models import Node5Config
from node4.node import run_node4
from node5.node import run_node5
from node5.rendering.html import render_html
from node5.validation.node4_validator import validate_node4_output
from schemas.enums import CustomerState
from schemas.node2 import ForwardHorizonResult
from schemas.node4 import ChurnedAccount, Node2EvidenceRef, Node4Output
from tests.node4.conftest import make_node2, make_node3, make_signal
from tests.node5.conftest import make_sample_node4

N_ACTIVE = 40


def _node4_v3() -> Node4Output:
    ids = [f"C-{i:03d}" for i in range(N_ACTIVE)] + ["X-GONE", "X-TAIL"]
    churn = [0.02] * (N_ACTIVE - 1) + [0.12]
    values: list[float | None] = [1.0 - p for p in churn]
    ci: list[list[float | None]] = [[1.0 - p - 0.01, 1.0 - p + 0.005] for p in churn]
    values += [None, None]
    ci += [[None, None], [None, None]]
    node2 = make_node2(ids, survival_90=[0.99] * len(ids), risk_scores=[0.01] * len(ids))
    node2 = node2.model_copy(
        update={
            "customer_tenure_days": [400.0] * N_ACTIVE + [100.0, 900.0],
            "customer_event_observed": [0] * N_ACTIVE + [1, 0],
            "forward_survival": {"90d": ForwardHorizonResult(values=values, ci=ci)},
            "max_follow_up_days": 950.0,
        }
    )
    # The orchestrator's no-support baseline: every customer no_data, zero threads.
    baseline = make_node3(
        [
            make_signal(
                cid,
                support_data_status="no_data",
                overall_signal_confidence=0.0,
                n_threads_in_window=0,
            )
            for cid in ids
        ]
    )
    return run_node4(node2, baseline, load_node4_config("3"))


@pytest.fixture(scope="module")
def node4_v3() -> Node4Output:
    return _node4_v3()


def test_report_copies_forward_lift_and_factors(
    node4_v3: Node4Output, node5_config: Node5Config, action_rules
) -> None:
    output = run_node5(node4_v3, node5_config, action_rules=action_rules)
    top = output.report.priority_accounts[0]
    source = node4_v3.ranked_accounts[0]
    assert top.customer_id == source.customer_id == "C-039"
    assert top.quantitative_summary.churn_prob_90d_forward == (
        source.quantitative.churn_prob_90d_forward
    )
    assert top.quantitative_summary.lift_vs_base == source.quantitative.lift_vs_base
    assert top.confidence_factors == source.confidence_factors
    assert "times the portfolio average" in top.summary


def test_churned_section_and_tail_notes(
    node4_v3: Node4Output, node5_config: Node5Config, action_rules
) -> None:
    output = run_node5(node4_v3, node5_config, action_rules=action_rules)
    assert output.report.churned.n_churned == 1
    assert output.report.churned.customer_ids == ["X-GONE"]
    assert output.processing_report.n_churned == 1
    ranked = {a.customer_id for a in output.report.priority_accounts}
    insufficient = {a.customer_id for a in output.report.insufficient_data_accounts}
    assert "X-GONE" not in ranked | insufficient
    assert "X-TAIL" in insufficient
    tail = next(a for a in output.report.insufficient_data_accounts if a.customer_id == "X-TAIL")
    assert any("follow-up" in note for note in tail.data_quality_notes)
    assert any("already churned" in note for note in output.report.data_quality.notes)
    assert "already churned" in output.report.executive_summary


def test_html_shows_churned_and_confidence(
    node4_v3: Node4Output, node5_config: Node5Config, action_rules
) -> None:
    html = render_html(run_node5(node4_v3, node5_config, action_rules=action_rules))
    assert '<section id="churned">' in html
    assert "X-GONE" in html
    assert "Confidence breakdown" in html
    assert "Lift vs portfolio average" in html


def test_v1_html_has_no_churned_section(node5_config: Node5Config, action_rules) -> None:
    html = render_html(run_node5(make_sample_node4(), node5_config, action_rules=action_rules))
    assert '<section id="churned">' not in html
    assert "Confidence breakdown" not in html


def test_validator_rejects_churned_overlap(node4_v3: Node4Output) -> None:
    ranked_id = node4_v3.ranked_accounts[0].customer_id
    bad = node4_v3.model_copy(
        update={
            "churned_accounts": [
                *node4_v3.churned_accounts,
                ChurnedAccount(
                    customer_id=ranked_id,
                    tenure_days=10.0,
                    evidence_refs=Node2EvidenceRef(
                        model_version="mv_test", customer_state=CustomerState.SCORED
                    ),
                ),
            ],
            "summary_stats": node4_v3.summary_stats.model_copy(update={"n_churned": 2}),
        }
    )
    codes = {error["code"] for error in validate_node4_output(bad).errors}
    assert "CROSS_LIST_MEMBERSHIP" in codes


def test_validator_rejects_churned_count_mismatch(node4_v3: Node4Output) -> None:
    bad = node4_v3.model_copy(
        update={"summary_stats": node4_v3.summary_stats.model_copy(update={"n_churned": 5})}
    )
    codes = {error["code"] for error in validate_node4_output(bad).errors}
    assert "SUMMARY_STATS_MISMATCH" in codes

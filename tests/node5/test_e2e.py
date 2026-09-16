"""Dataset 7 end-to-end: Node 1 -> Node 2 -> Node 3 -> Node 4 -> Node 5.

The committed corpus exercises the real master data through all five nodes and
asserts the Node 5 invariants (architecture §5.34 / ROADMAP Task 6.12). LLM is
disabled, so the report is fully deterministic.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from config.loader import (
    load_action_rules,
    load_node1_config,
    load_node2_config,
    load_node3_config,
    load_node4_config,
    load_node5_config,
    load_vocabulary,
)
from node1.node import run_node1
from node2.node import run_node2
from node3.node import run_node3
from node4.node import run_node4
from node5.node import run_node5
from node5.report.consistency import check_consistency
from schemas.node3 import Node3Output
from schemas.node4 import Node4Output
from schemas.node5 import Node5Output

REPO = Path(__file__).resolve().parents[2]
RAW_CSV = REPO / "data" / "raw" / "dataset7_customers_messy.csv"
THREADS_JSON = REPO / "data" / "raw" / "dataset7_support_threads_messy.json"
TRUTH_JSON = REPO / "data" / "ground_truth" / "dataset7_ground_truth.json"

PREDICTORS = ["plan_tier", "contract_length_months", "usage_frequency", "support_tickets_90d"]
NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def pipeline() -> tuple[Node3Output, Node4Output]:
    node1_output = run_node1(RAW_CSV, config=load_node1_config("dataset7"))
    dataset = node1_output.canonical_dataset
    node2_output = run_node2(dataset, load_node2_config("1"), PREDICTORS)
    customers = [record.customer_id for record in dataset]
    threads = json.loads(THREADS_JSON.read_text(encoding="utf-8"))
    node3_output = run_node3(
        customers,
        threads,
        load_node3_config("dataset7"),
        vocabulary=load_vocabulary(),
        now=NOW,
    )
    node4_output = run_node4(node2_output, node3_output, load_node4_config("1"))
    return node3_output, node4_output


@pytest.fixture(scope="module")
def report(pipeline) -> Node5Output:
    node3_output, node4_output = pipeline
    return run_node5(
        node4_output,
        load_node5_config("dataset7"),
        node3_output=node3_output,
        action_rules=load_action_rules("1"),
    )


def test_node5_preserves_node4_statistics(pipeline, report: Node5Output) -> None:
    _, node4_output = pipeline
    assert report.report.risk_distribution.critical == node4_output.summary_stats.n_critical
    assert report.report.risk_distribution.high == node4_output.summary_stats.n_high
    assert report.report.risk_distribution.medium == node4_output.summary_stats.n_medium
    assert report.report.risk_distribution.low == node4_output.summary_stats.n_low
    assert (
        report.report.risk_distribution.insufficient_data
        == node4_output.summary_stats.n_insufficient_data
    )


def test_node5_preserves_node4_ordering(pipeline, report: Node5Output) -> None:
    _, node4_output = pipeline
    assert [a.customer_id for a in report.report.priority_accounts] == [
        a.customer_id for a in node4_output.ranked_accounts
    ]


def test_node5_copies_decision_fields(pipeline, report: Node5Output) -> None:
    _, node4_output = pipeline
    by_id = {a.customer_id: a for a in node4_output.ranked_accounts}
    for entry in report.report.priority_accounts:
        source = by_id[entry.customer_id]
        assert entry.rank == source.rank
        assert entry.risk_level.value == source.combined_risk_level.value
        assert entry.combined_score == source.combined_score
        assert entry.combined_confidence == source.combined_confidence


def test_node5_separates_insufficient(pipeline, report: Node5Output) -> None:
    main = {a.customer_id for a in report.report.priority_accounts}
    insufficient = {a.customer_id for a in report.report.insufficient_data_accounts}
    assert main.isdisjoint(insufficient)
    for entry in report.report.insufficient_data_accounts:
        assert entry.rank is None
        assert entry.risk_level.value == "insufficient_data"


def test_node5_retains_critical_reasons(report: Node5Output) -> None:
    critical = [a for a in report.report.priority_accounts if a.risk_level.value == "critical"]
    assert critical
    for entry in critical:
        assert any(
            reason.reason_type.value.startswith("critical_")
            for reason in entry.primary_reasons
        )


def test_node5_reference_date_and_generated_at(report: Node5Output) -> None:
    assert report.metadata.reference_date.isoformat() == "2026-08-15"
    assert report.metadata.generated_at.isoformat().startswith("2026-08-15T00:00:00")


def test_node5_consistency_gate(pipeline, report: Node5Output) -> None:
    _, node4_output = pipeline
    assert check_consistency(report, node4_output) == []


def test_node5_deterministic_rerun(pipeline, report: Node5Output) -> None:
    node3_output, node4_output = pipeline
    rerun = run_node5(
        node4_output,
        load_node5_config("dataset7"),
        node3_output=node3_output,
        action_rules=load_action_rules("1"),
    )
    assert rerun.model_dump(mode="json") == report.model_dump(mode="json")


def test_node5_preserves_node4_level_for_trap_customers(
    pipeline, report: Node5Output
) -> None:
    """Node 5 must not alter Node 4 levels for the trap cohort.

    The dataset-7 ``node5_trap_oracle`` is a *design-intent* oracle whose
    ``expected_risk_level`` uses display thresholds / a reason vocabulary that
    differ from the contract pipeline (same limitation documented in
    ``tests/node4/test_e2e.py``). Against the real Node 4 output, 20 of 40 trap
    customers (traps 001 usage_drop and 002 billing_complaint) are classified one
    band lower than the oracle expects — a Node 4/oracle discrepancy that
    **Node 5 must surface, not fix**. This test therefore asserts the authoritative
    invariant: displayed level == Node 4 level for every trap customer.
    """
    _, node4_output = pipeline
    truth = json.loads(TRUTH_JSON.read_text(encoding="utf-8"))
    oracle = truth["node5_trap_oracle"]
    node4_level = {
        entry.customer_id: entry.combined_risk_level.value
        for entry in [
            *node4_output.ranked_accounts,
            *node4_output.insufficient_data_accounts,
        ]
    }
    reported = {
        entry.customer_id: entry.risk_level.value
        for entry in [
            *report.report.priority_accounts,
            *report.report.insufficient_data_accounts,
        ]
    }
    checked = 0
    for customer_id in oracle:
        if customer_id not in reported:
            continue
        assert reported[customer_id] == node4_level[customer_id], customer_id
        checked += 1
    assert checked == len(oracle)

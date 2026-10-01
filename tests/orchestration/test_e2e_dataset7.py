"""Dataset 7 end-to-end through the orchestrator (ROADMAP Task 7.3).

One real run of the committed master corpus through ``run_pipeline`` — Node 1 ->
Node 2 -> Node 3 -> Node 4 -> Node 5 — asserting the frozen Node 5 invariants
hold (distribution + ordering preserved from Node 4).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from orchestration.graph import run_pipeline
from orchestration.state import PipelineStatus

REPO = Path(__file__).resolve().parents[2]
RAW_CSV = REPO / "data" / "raw" / "dataset7_customers_messy.csv"
THREADS_JSON = REPO / "data" / "raw" / "dataset7_support_threads_messy.json"


def test_dataset7_full_pipeline_completes() -> None:
    threads = json.loads(THREADS_JSON.read_text(encoding="utf-8"))
    result = run_pipeline(
        RAW_CSV,
        node1_version="dataset7",
        node3_version="dataset7",
        node5_version="dataset7",
        support_data=threads,
        action_rules=None,
        reference_date=date(2026, 8, 15),
    )

    assert result.status is PipelineStatus.COMPLETED
    state = result.state
    node1 = state.node1_output
    node4 = state.node4_output
    node5 = state.node5_output
    assert node1 is not None and node4 is not None and node5 is not None
    assert node1.validation_report.status.value == "PARTIAL"

    distribution = node5.report.risk_distribution
    assert distribution.critical == node4.summary_stats.n_critical
    assert distribution.high == node4.summary_stats.n_high
    assert distribution.medium == node4.summary_stats.n_medium
    assert distribution.low == node4.summary_stats.n_low
    assert distribution.insufficient_data == node4.summary_stats.n_insufficient_data

    assert [a.customer_id for a in node5.report.priority_accounts] == [
        a.customer_id for a in node4.ranked_accounts
    ]
    assert state.artifact_dir is None  # persist_artifact defaults to False

"""Dataset 7 end-to-end smoke test: Node 1 → Node 2 → Node 3 → Node 4.

The committed E2E exercises the real master corpus through all four nodes and
asserts the Node 4 invariants. The Dataset 7 ``node4_scenario_oracle`` uses
different display thresholds and a non-contract reason vocabulary, so it is not
asserted strictly here (see the implementation report).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from config.loader import (
    load_node1_config,
    load_node2_config,
    load_node3_config,
    load_node4_config,
    load_vocabulary,
)
from node1.node import run_node1
from node2.node import run_node2
from node3.node import run_node3
from node4.node import run_node4
from schemas.node4 import Node4Output

pytestmark = pytest.mark.usefixtures("dataset7_corpus")

REPO = Path(__file__).resolve().parents[2]
RAW_CSV = REPO / "data" / "raw" / "dataset7_customers_messy.csv"
THREADS_JSON = REPO / "data" / "raw" / "dataset7_support_threads_messy.json"

PREDICTORS = ["plan_tier", "contract_length_months", "usage_frequency", "support_tickets_90d"]
NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def dataset7_node4_output() -> Node4Output:
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
    return run_node4(node2_output, node3_output, load_node4_config("1"))


def _ids(output: Node4Output) -> list[str]:
    return [a.customer_id for a in output.ranked_accounts] + [
        a.customer_id for a in output.insufficient_data_accounts
    ]


def test_dataset7_universe_is_covered_once(dataset7_node4_output: Node4Output) -> None:
    ids = _ids(dataset7_node4_output)
    assert len(ids) == len(set(ids))
    assert dataset7_node4_output.summary_stats.n_customers == len(ids)
    # Node 1/2 accepted 4680 records; Node 3 was run over the same universe.
    assert len(ids) == 4680


def test_dataset7_summary_stats_match_lists(dataset7_node4_output: Node4Output) -> None:
    output = dataset7_node4_output
    stats = output.summary_stats
    assert stats.n_critical + stats.n_high + stats.n_medium + stats.n_low == len(
        output.ranked_accounts
    )
    assert stats.n_insufficient_data == len(output.insufficient_data_accounts)
    assert stats.n_customers == len(output.ranked_accounts) + len(
        output.insufficient_data_accounts
    )


def test_dataset7_no_cross_list_membership(dataset7_node4_output: Node4Output) -> None:
    main = {a.customer_id for a in dataset7_node4_output.ranked_accounts}
    insufficient = {a.customer_id for a in dataset7_node4_output.insufficient_data_accounts}
    assert main.isdisjoint(insufficient)


def test_dataset7_records_reference_date(dataset7_node4_output: Node4Output) -> None:
    assert dataset7_node4_output.reference_date.isoformat() == "2026-08-15"


def test_dataset7_every_critical_has_a_critical_reason(
    dataset7_node4_output: Node4Output,
) -> None:
    critical = [
        a
        for a in dataset7_node4_output.ranked_accounts
        if a.combined_risk_level.value == "critical"
    ]
    assert critical
    for account in critical:
        assert any(
            reason.reason_type.value.startswith("critical_")
            for reason in account.primary_reasons
        )


def test_dataset7_deterministic_rerun(dataset7_node4_output: Node4Output) -> None:
    config = load_node4_config("1")
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
    rerun = run_node4(node2_output, node3_output, config)
    assert rerun.model_dump(mode="json") == dataset7_node4_output.model_dump(mode="json")

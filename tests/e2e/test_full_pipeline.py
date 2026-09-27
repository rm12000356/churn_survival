"""Full-pipeline E2E: dataset 7 persisted + byte-identical across two runs.

ROADMAP Task 8.3 (determinism across two full runs).
"""

from __future__ import annotations

import json
from pathlib import Path

from config.settings import Settings
from orchestration.graph import run_pipeline
from orchestration.persistence import RunStore
from orchestration.state import PipelineStatus
from tests.e2e.conftest import (
    DATASET7_CSV,
    DATASET7_THREADS,
    DATASET7_VERSIONS,
    REFERENCE_DATE,
)


def _run(reference_date, settings, **kwargs):
    return run_pipeline(
        DATASET7_CSV,
        reference_date=reference_date,
        settings=settings,
        config_dir=settings.CONFIG_DIR,
        **DATASET7_VERSIONS,
        **kwargs,
    )


def test_dataset7_full_pipeline_persisted_and_deterministic(
    e2e_settings: Settings, tmp_path: Path
) -> None:
    threads = json.loads(DATASET7_THREADS.read_text(encoding="utf-8"))

    store_a = RunStore(tmp_path / "a")
    first = _run(REFERENCE_DATE, e2e_settings, support_data=threads, action_rules=None)
    assert first.status is PipelineStatus.COMPLETED
    store_a.save(first)

    store_b = RunStore(tmp_path / "b")
    second = _run(REFERENCE_DATE, e2e_settings, support_data=threads, action_rules=None)
    store_b.save(second)

    # Determinism: same identity and byte-identical report outputs.
    assert first.state.run_id is not None
    assert first.state.run_id == second.state.run_id
    run_id = first.state.run_id
    assert (
        store_a.run_dir(run_id) / "node5.json"
    ).read_bytes() == (store_b.run_dir(run_id) / "node5.json").read_bytes()
    assert (
        store_a.run_dir(run_id) / "report.html"
    ).read_bytes() == (store_b.run_dir(run_id) / "report.html").read_bytes()

    # Node 4 decisions are copied verbatim by Node 5 (distribution + order).
    node4 = first.state.node4_output
    node5 = first.state.node5_output
    assert node4 is not None and node5 is not None
    distribution = node5.report.risk_distribution
    assert distribution.critical == node4.summary_stats.n_critical
    assert distribution.high == node4.summary_stats.n_high
    assert distribution.medium == node4.summary_stats.n_medium
    assert distribution.low == node4.summary_stats.n_low
    assert distribution.insufficient_data == node4.summary_stats.n_insufficient_data
    assert [a.customer_id for a in node5.report.priority_accounts] == [
        a.customer_id for a in node4.ranked_accounts
    ]

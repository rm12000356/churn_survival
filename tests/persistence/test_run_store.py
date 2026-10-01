"""Run store + index + GC tests (ROADMAP Phase 8, Task 8.1)."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from orchestration.gc import prune_runs, recover_stale_running
from orchestration.graph import run_pipeline
from orchestration.index import RunIndex
from orchestration.persistence import RunStore
from schemas.run import (
    RoutingIdentitySource,
    RunExecutionStatus,
    RunSummary,
)


def _save_run(store: RunStore, raw_path: Path):
    result = run_pipeline(raw_path)
    assert result.state.run_id
    store.save(result)
    return result


def test_save_load_and_artifacts(tmp_path: Path, clean_csv: Path) -> None:
    store = RunStore(tmp_path)
    result = _save_run(store, clean_csv)
    run_id = result.state.run_id
    assert run_id is not None

    target = store.run_dir(run_id)
    for name in ("state.json", "summary.json", "node5.json", "report.html"):
        assert (target / name).is_file(), name

    reloaded = store.load(run_id)
    assert reloaded.state.run_id == run_id
    assert store.read_node_output(run_id, "node4") is not None
    assert store.read_report_html(run_id) is not None
    assert store.read_node_output(run_id, "node1") is not None


def test_summary_reports_completed(tmp_path: Path, clean_csv: Path) -> None:
    store = RunStore(tmp_path)
    result = _save_run(store, clean_csv)
    summary = store.get_summary(result.state.run_id or "")
    assert summary is not None
    assert summary.execution_status == RunExecutionStatus.COMPLETED
    assert summary.pipeline_status == "COMPLETED"
    assert summary.routing_identity_source == RoutingIdentitySource.COMPUTED
    assert summary.routing_adapter is not None


def test_two_runs_are_byte_identical(tmp_path: Path, clean_csv: Path) -> None:
    store = RunStore(tmp_path / "a")
    first = _save_run(store, clean_csv)
    first_report = (store.run_dir(first.state.run_id or "") / "node5.json").read_bytes()

    store2 = RunStore(tmp_path / "b")
    second = _save_run(store2, clean_csv)
    second_report = (store2.run_dir(second.state.run_id or "") / "node5.json").read_bytes()

    assert first.state.run_id == second.state.run_id
    assert first_report == second_report


def test_list_runs_self_heals_from_disk(tmp_path: Path, clean_csv: Path) -> None:
    store = RunStore(tmp_path)
    result = _save_run(store, clean_csv)

    (tmp_path / "index.sqlite").unlink()
    healed = RunStore(tmp_path)
    runs = healed.list_runs()
    assert [run.run_id for run in runs] == [result.state.run_id]


def test_legacy_row_surfaces_unknown_routing(tmp_path: Path, clean_csv: Path) -> None:
    store = RunStore(tmp_path)
    result = _save_run(store, clean_csv)
    run_id = result.state.run_id or ""
    # Simulate a pre-migration run: no summary sidecar, no routing metadata.
    (store.run_dir(run_id) / "summary.json").unlink()
    (tmp_path / "index.sqlite").unlink()

    healed = RunStore(tmp_path)
    healed.list_runs()
    summary = healed.get_summary(run_id)
    assert summary is not None
    assert summary.routing_identity_source == RoutingIdentitySource.UNKNOWN_PRE_MIGRATION
    assert summary.routing_adapter is None
    assert summary.routing_adapter_version is None


def test_index_upsert_get_list(tmp_path: Path) -> None:
    index = RunIndex(tmp_path / "index.sqlite")
    index.upsert(RunSummary(run_id="r1", model_version="m1"))
    index.upsert(RunSummary(run_id="r2", model_version="m2"))
    assert index.get("r1") is not None
    assert [r.run_id for r in index.list(model_version="m2")] == ["r2"]
    assert {r.run_id for r in index.all()} == {"r1", "r2"}


def test_index_mark_stale_running_interrupted(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    run_id = "stale"
    store.index.upsert(RunSummary(run_id=run_id, execution_status=RunExecutionStatus.RUNNING))
    assert recover_stale_running(store) == 1
    summary = store.get_summary(run_id)
    assert summary is not None
    assert summary.execution_status == RunExecutionStatus.INTERRUPTED


def test_delete_removes_dir_and_index_row(tmp_path: Path, clean_csv: Path) -> None:
    store = RunStore(tmp_path)
    result = _save_run(store, clean_csv)
    run_id = result.state.run_id or ""
    store.delete(run_id)
    assert not store.run_dir(run_id).exists()
    assert store.get_summary(run_id) is None


def test_read_unknown_node_raises(tmp_path: Path, clean_csv: Path) -> None:
    store = RunStore(tmp_path)
    result = _save_run(store, clean_csv)
    import pytest

    with pytest.raises(ValueError):
        store.read_node_output(result.state.run_id or "", "node9")


def test_gc_count_prune_keeps_newest(tmp_path: Path, clean_csv: Path) -> None:
    store = RunStore(tmp_path)
    r1 = _save_run(store, clean_csv)
    # Distinct identity via a different reference date.
    r2 = run_pipeline(clean_csv, reference_date=date(2026, 8, 16))
    store.save(r2)
    old = datetime.now(UTC) - timedelta(days=30)
    # Recency comes from the run's summary sidecar, not the directory mtime.
    os.utime(
        store.run_dir(r1.state.run_id or "") / "summary.json", (old.timestamp(), old.timestamp())
    )

    deleted = prune_runs(store, max_runs=1, pending_ttl_days=0)
    assert r1.state.run_id in deleted
    assert r2.state.run_id not in deleted
    assert not store.run_dir(r1.state.run_id or "").exists()


def test_gc_in_place_rerun_counts_as_new(tmp_path: Path, clean_csv: Path) -> None:
    """Regression: a re-run rewrites files in place; that run is the newest, not the oldest."""
    store = RunStore(tmp_path)
    r1 = _save_run(store, clean_csv)
    r2 = run_pipeline(clean_csv, reference_date=date(2026, 8, 16))
    store.save(r2)
    old = (datetime.now(UTC) - timedelta(days=30)).timestamp()
    os.utime(store.run_dir(r1.state.run_id or ""), (old, old))  # directory looks old
    store.save(r1)  # re-run in place: files are rewritten, directory mtime untouched
    os.utime(store.run_dir(r1.state.run_id or ""), (old, old))
    os.utime(store.run_dir(r2.state.run_id or "") / "summary.json", (old, old))

    deleted = prune_runs(store, max_runs=1, pending_ttl_days=0)
    assert deleted == [r2.state.run_id]


def test_gc_never_prunes_in_flight_runs(tmp_path: Path, clean_csv: Path) -> None:
    store = RunStore(tmp_path)
    r1 = _save_run(store, clean_csv)
    r2 = run_pipeline(clean_csv, reference_date=date(2026, 8, 16))
    store.save(r2)
    assert store.index is not None
    store.index.update_fields(r1.state.run_id or "", execution_status=RunExecutionStatus.RUNNING)
    old = (datetime.now(UTC) - timedelta(days=30)).timestamp()
    os.utime(store.run_dir(r1.state.run_id or "") / "summary.json", (old, old))

    assert prune_runs(store, max_runs=1, pending_ttl_days=0) == []
    assert store.run_dir(r1.state.run_id or "").exists()


def test_gc_ttl_prunes_pending_states(tmp_path: Path, unmapped_csv: Path) -> None:
    store = RunStore(tmp_path)
    result = _save_run(store, unmapped_csv)
    assert result.state.status is not None
    run_id = result.state.run_id or ""
    summary = store.get_summary(run_id)
    assert summary is not None
    assert summary.execution_status == RunExecutionStatus.STOPPED_NEEDS_MAPPING

    old = datetime.now(UTC) - timedelta(days=30)
    os.utime(store.run_dir(run_id) / "summary.json", (old.timestamp(), old.timestamp()))

    deleted = prune_runs(store, max_runs=0, pending_ttl_days=7, now=datetime.now(UTC))
    assert run_id in deleted
    assert not store.run_dir(run_id).exists()
    assert store.get_summary(run_id) is None

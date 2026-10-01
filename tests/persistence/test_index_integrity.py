"""Run index / store integrity regressions (REVIEW.md H5, H6, M-O1, M-O2, M-O3)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from orchestration.graph import run_pipeline
from orchestration.index import SCHEMA_VERSION, RunIndex
from orchestration.persistence import RunStore
from schemas.run import RoutingIdentitySource, RunExecutionStatus, RunSummary

STAMP = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _row(run_id: str, status: RunExecutionStatus, **extra: object) -> RunSummary:
    return RunSummary(
        run_id=run_id,
        execution_status=status,
        routing_identity_source=RoutingIdentitySource.COMPUTED,
        **extra,
    )


def test_save_keeps_bookkeeping_fields(tmp_path: Path, clean_csv: Path) -> None:
    """H5: saving a result never erases created_at/started_at/superseded_by."""
    store = RunStore(tmp_path)
    result = run_pipeline(clean_csv)
    run_id = result.state.run_id or ""
    assert store.index is not None
    store.index.upsert(
        _row(run_id, RunExecutionStatus.RUNNING, created_at=STAMP, started_at=STAMP)
    )
    store.index.update_fields(run_id, superseded_by="newer-run")

    store.save(result)

    saved = store.get_summary(run_id)
    assert saved is not None
    assert saved.execution_status == RunExecutionStatus.COMPLETED
    assert saved.created_at == STAMP and saved.started_at == STAMP
    assert saved.superseded_by == "newer-run"
    # The disk sidecar carries them too, so a rebuild cannot lose them.
    sidecar = json.loads((store.run_dir(run_id) / "summary.json").read_text(encoding="utf-8"))
    assert sidecar["created_at"] is not None


def test_filtered_listing_never_rewrites_the_index(tmp_path: Path, clean_csv: Path) -> None:
    """H6: a filter that matches nothing is an answer, not a reason to rebuild."""
    store = RunStore(tmp_path)
    result = run_pipeline(clean_csv)
    store.save(result)
    run_id = result.state.run_id or ""
    assert store.index is not None
    store.index.update_fields(run_id, execution_status=RunExecutionStatus.INTERRUPTED)

    assert store.list_runs(status=RunExecutionStatus.FAILED) == []

    current = store.get_summary(run_id)
    assert current is not None
    assert current.execution_status == RunExecutionStatus.INTERRUPTED


def test_empty_index_self_heals_from_disk(tmp_path: Path, clean_csv: Path) -> None:
    store = RunStore(tmp_path)
    result = run_pipeline(clean_csv)
    store.save(result)
    (tmp_path / "index.sqlite").unlink()
    for suffix in ("-wal", "-shm"):
        (tmp_path / f"index.sqlite{suffix}").unlink(missing_ok=True)

    fresh = RunStore(tmp_path)
    assert [run.run_id for run in fresh.list_runs()] == [result.state.run_id]


def test_claim_is_exclusive(tmp_path: Path) -> None:
    """M-O1: two triggers for the same identity -> exactly one enqueues."""
    index = RunIndex(tmp_path / "index.sqlite")
    pending = _row("run-a", RunExecutionStatus.PENDING, created_at=STAMP)
    assert index.claim(pending) is True
    assert index.claim(pending) is False
    index.update_fields("run-a", execution_status=RunExecutionStatus.FAILED)
    assert index.claim(pending) is True  # a failed run may be resubmitted


def test_rerun_that_stops_earlier_drops_stale_outputs(
    tmp_path: Path, clean_csv: Path
) -> None:
    """M-O2: no stale report next to a newer, shorter result."""
    store = RunStore(tmp_path)
    result = run_pipeline(clean_csv)
    store.save(result)
    run_dir = store.run_dir(result.state.run_id or "")
    assert (run_dir / "report.html").is_file()

    result.state.node5_output = None
    result.state.node4_output = None
    store.save(result)
    assert not (run_dir / "report.html").exists()
    assert not (run_dir / "node4.json").exists()
    assert not list(run_dir.glob(".*"))  # no temp files left behind


def test_old_index_schema_is_migrated(tmp_path: Path) -> None:
    """M-O3: a DB missing a column gains it instead of failing every upsert."""
    db = tmp_path / "index.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, execution_status TEXT NOT NULL, "
        "routing_identity_source TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()

    index = RunIndex(db)
    index.upsert(_row("run-b", RunExecutionStatus.COMPLETED, created_at=STAMP))
    assert index.get("run-b") is not None
    check = sqlite3.connect(db)
    try:
        assert check.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        check.close()


def test_undecodable_row_is_skipped_not_fatal(tmp_path: Path) -> None:
    """L6: one bad row does not take the listing down."""
    index = RunIndex(tmp_path / "index.sqlite")
    index.upsert(_row("good", RunExecutionStatus.COMPLETED, created_at=STAMP))
    conn = sqlite3.connect(tmp_path / "index.sqlite")
    try:
        conn.execute(
            "INSERT INTO runs (run_id, execution_status, routing_identity_source) "
            "VALUES ('bad', 'NOT_A_STATUS', 'computed')"
        )
        conn.commit()
    finally:
        conn.close()
    assert [run.run_id for run in index.list()] == ["good"]
    assert index.count() == 2


def test_stale_recovery_covers_pending_runs(tmp_path: Path) -> None:
    index = RunIndex(tmp_path / "index.sqlite")
    index.upsert(_row("queued", RunExecutionStatus.PENDING, created_at=STAMP))
    assert index.mark_stale_running_interrupted() == 1
    recovered = index.get("queued")
    assert recovered is not None
    assert recovered.execution_status == RunExecutionStatus.INTERRUPTED

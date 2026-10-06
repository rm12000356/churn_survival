"""GC / retention tests (ROADMAP Phase 8, Task 8.1 / D-P6)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orchestration import gc
from orchestration.persistence import RunStore
from schemas.run import RunExecutionStatus, RunSummary


def _completed(run_id: str, *, model_version: str, finished_at: datetime) -> RunSummary:
    return RunSummary(
        run_id=run_id,
        execution_status=RunExecutionStatus.COMPLETED,
        pipeline_status="COMPLETED",
        model_version=model_version,
        created_at=finished_at,
        finished_at=finished_at,
    )


def _seed_run_dir(store: RunStore, run_id: str) -> None:
    run_dir = store.base_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text("{}", encoding="utf-8")


def test_prune_mapping_drafts_only_old(tmp_path: Path) -> None:
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    old = drafts / "old.json"
    old.write_text("{}", encoding="utf-8")
    new = drafts / "new.json"
    new.write_text("{}", encoding="utf-8")
    old_ts = (datetime.now(UTC) - timedelta(days=30)).timestamp()
    os.utime(old, (old_ts, old_ts))

    deleted = gc.prune_mapping_drafts(drafts, 7, now=datetime.now(UTC))
    assert deleted == [old]
    assert new.exists()
    assert gc.prune_mapping_drafts(drafts, 0) == []  # disabled


def test_prune_model_artifacts_keeps_newest(tmp_path: Path) -> None:
    base = tmp_path / "models"
    for index in range(3):
        artifact = base / f"m{index}"
        artifact.mkdir(parents=True)
        (artifact / "model.json").write_text("{}", encoding="utf-8")
        # Recency is the sidecar's mtime (rewritten on every save/refit).
        os.utime(artifact / "model.json", (index + 1, index + 1))  # m0 oldest, m2 newest

    deleted = gc.prune_model_artifacts(base, 1)
    assert sorted(path.name for path in deleted) == ["m0", "m1"]
    assert (base / "m2").exists()
    assert gc.prune_model_artifacts(base, 0) == []


def test_prune_model_artifacts_keeps_models_retained_runs_use(tmp_path: Path) -> None:
    base = tmp_path / "models"
    for index in range(3):
        artifact = base / f"m{index}"
        artifact.mkdir(parents=True)
        (artifact / "model.json").write_text("{}", encoding="utf-8")
        os.utime(artifact / "model.json", (index + 1, index + 1))

    deleted = gc.prune_model_artifacts(base, 1, protected={"m0"})
    assert [path.name for path in deleted] == ["m1"]
    assert (base / "m0").exists()


def test_prune_runs_then_models_keeps_a_shared_model(tmp_path: Path) -> None:
    """A model referenced by a surviving run survives even if a pruned run used it."""
    store = RunStore(tmp_path / "runs")
    base = tmp_path / "models"
    for name, mtime in (("m_shared", 1), ("m_other", 10)):
        artifact = base / name
        artifact.mkdir(parents=True)
        (artifact / "model.json").write_text("{}", encoding="utf-8")
        os.utime(artifact / "model.json", (mtime, mtime))

    old = datetime(2026, 1, 1, tzinfo=UTC)
    new = datetime(2026, 6, 1, tzinfo=UTC)
    _seed_run_dir(store, "old")
    _seed_run_dir(store, "new")
    store.index.upsert(_completed("old", model_version="m_shared", finished_at=old))
    store.index.upsert(_completed("new", model_version="m_shared", finished_at=new))

    assert gc.prune_runs(store, max_runs=1, pending_ttl_days=0) == ["old"]

    protected = gc.referenced_model_versions(store)
    assert protected == {"m_shared"}
    # m_shared is the oldest (would be pruned at max_count=1); its reference by the
    # surviving run must keep it.
    deleted = [path.name for path in gc.prune_model_artifacts(base, 1, protected=protected)]
    assert deleted == []
    assert (base / "m_shared").exists()
    assert (base / "m_other").exists()


def test_prune_runs_skips_in_flight_and_protects_its_model(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "runs")
    for run_id in ("running", "done_new", "done_old"):
        _seed_run_dir(store, run_id)
    store.index.upsert(
        RunSummary(
            run_id="running",
            execution_status=RunExecutionStatus.RUNNING,
            model_version="m_running",
        )
    )
    store.index.upsert(
        _completed("done_new", model_version="m_done", finished_at=datetime(2026, 6, 1, tzinfo=UTC))
    )
    store.index.upsert(
        _completed("done_old", model_version="m_done", finished_at=datetime(2026, 1, 1, tzinfo=UTC))
    )

    pruned = gc.prune_runs(store, max_runs=1, pending_ttl_days=0)
    assert "running" not in pruned
    assert pruned == ["done_old"]
    assert gc.referenced_model_versions(store) == {"m_running", "m_done"}


def test_in_flight_run_without_a_recorded_model_is_not_protected(tmp_path: Path) -> None:
    """Documents a residual gap: a RUNNING run with no model_version yet
    contributes nothing to referenced_model_versions, so its artifact could be
    pruned. Recorded here so a future fix has a concrete target."""
    store = RunStore(tmp_path / "runs")
    _seed_run_dir(store, "running")
    store.index.upsert(
        RunSummary(run_id="running", execution_status=RunExecutionStatus.RUNNING)
    )
    assert gc.referenced_model_versions(store) == set()


def test_gc_main_flags(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "runs"
    code = gc.main(
        [
            "--run-dir",
            str(run_dir),
            "--model-dir",
            str(tmp_path / "models"),
            "--models",
            "1",
            "--runs",
            "1",
            "--pending-ttl-days",
            "0",
            "--recover",
        ]
    )
    assert code == 0
    assert "GC:" in capsys.readouterr().out


def test_gc_main_missing_value_is_usage_error() -> None:
    assert gc.main(["--models"]) == 2

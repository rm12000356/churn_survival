"""App factory / startup recovery tests (ROADMAP Phase 8)."""

from __future__ import annotations

from api.app import create_app
from config.settings import Settings
from orchestration.persistence import RunStore
from schemas.run import RunExecutionStatus, RunSummary
from tests.api.conftest import SyncExecutor


def test_startup_recovery_marks_stale_running(
    api_settings: Settings,
) -> None:
    settings = api_settings.model_copy(
        update={
            "GC_ON_STARTUP": True,
            "RUN_RETENTION_MAX": 0,
            "MODEL_RETENTION_MAX": 0,
            "PENDING_RUN_TTL_DAYS": 0,
        }
    )
    store = RunStore(settings.RUN_DIR)
    store.index.upsert(
        RunSummary(run_id="stale", execution_status=RunExecutionStatus.RUNNING)
    )

    create_app(
        settings, store=store, executor=SyncExecutor(), run_startup_recovery=True
    )

    recovered = store.get_summary("stale")
    assert recovered is not None
    assert recovered.execution_status == RunExecutionStatus.INTERRUPTED


def test_second_process_skips_startup_recovery(api_settings: Settings) -> None:
    # REVIEW LOW: recovery is only safe for the single owner of the run store.
    store = RunStore(api_settings.RUN_DIR)
    first = create_app(api_settings, store=store, executor=SyncExecutor())
    try:
        store.index.upsert(
            RunSummary(run_id="live", execution_status=RunExecutionStatus.RUNNING)
        )
        create_app(api_settings, store=store, executor=SyncExecutor())  # e.g. a 2nd worker
        live = store.get_summary("live")
        assert live is not None
        assert live.execution_status == RunExecutionStatus.RUNNING
    finally:
        first.state.owner_lock.release()


def test_stale_owner_lock_is_taken_over(api_settings: Settings) -> None:
    import os
    import time

    from api.app import StoreOwnerLock

    lock_path = api_settings.RUN_DIR / ".api-owner.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("99999", encoding="utf-8")
    old = time.time() - StoreOwnerLock.STALE_S - 10
    os.utime(lock_path, (old, old))
    lock = StoreOwnerLock(api_settings.RUN_DIR)
    try:
        assert lock.acquire()
    finally:
        lock.release()
    assert not lock_path.exists()

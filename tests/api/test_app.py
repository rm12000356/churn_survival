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

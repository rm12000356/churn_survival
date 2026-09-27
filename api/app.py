"""FastAPI application factory (ROADMAP Phase 8, Task 8.2).

The API is a **serving layer**: read endpoints only deserialize stored outputs;
the single computing trigger is ``POST /runs``, which enqueues one background
``run_pipeline`` execution. A single-worker executor serializes CPU-bound fits
and SQLite writes by default.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from api.routes import health, mappings, models, runs
from config.settings import Settings, get_settings
from orchestration.persistence import RunStore

__all__ = ["create_app"]


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        executor: Any = getattr(app.state, "executor", None)
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)


def _startup_recovery(store: RunStore, settings: Settings) -> None:
    """Mark stale RUNNING rows INTERRUPTED; optionally run GC on startup."""
    store.mark_stale_running_interrupted()
    if not settings.GC_ON_STARTUP:
        return
    from orchestration.gc import (
        prune_mapping_drafts,
        prune_model_artifacts,
        prune_runs,
    )

    prune_runs(
        store,
        max_runs=settings.RUN_RETENTION_MAX,
        pending_ttl_days=settings.PENDING_RUN_TTL_DAYS,
    )
    prune_model_artifacts(settings.MODEL_DIR, settings.MODEL_RETENTION_MAX)
    prune_mapping_drafts(
        Path(settings.CONFIG_DIR) / "mappings" / "drafts",
        settings.PENDING_RUN_TTL_DAYS,
    )


def create_app(
    settings: Settings | None = None,
    *,
    store: RunStore | None = None,
    executor: Any | None = None,
    run_startup_recovery: bool = True,
) -> FastAPI:
    """Build the API app; all collaborators are injectable for tests."""
    resolved = settings or get_settings()
    resolved_store = store or RunStore(resolved.RUN_DIR)
    resolved_executor = executor or ThreadPoolExecutor(
        max_workers=resolved.RUN_MAX_WORKERS, thread_name_prefix="churn-run"
    )
    if run_startup_recovery:
        _startup_recovery(resolved_store, resolved)

    app = FastAPI(title="Churn Survival API", version="0.1.0", lifespan=_lifespan)
    app.state.settings = resolved
    app.state.store = resolved_store
    app.state.executor = resolved_executor
    app.include_router(health.router)
    app.include_router(runs.router)
    app.include_router(models.router)
    app.include_router(mappings.router)
    return app

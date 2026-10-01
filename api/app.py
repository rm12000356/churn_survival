"""FastAPI application factory (ROADMAP Phase 8, Task 8.2).

The API is a **serving layer**: read endpoints only deserialize stored outputs;
the single computing trigger is ``POST /runs``, which enqueues one background
``run_pipeline`` execution. A single-worker executor serializes CPU-bound fits
and SQLite writes by default.
"""

from __future__ import annotations

import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from api.deps import require_auth
from api.routes import configs, health, mappings, models, runs, uploads
from api.routes.uploads import MAX_UPLOAD_BYTES
from config.settings import Settings, get_settings
from logging_setup import bind_request_context, clear_run_context, get_logger
from orchestration.persistence import RunStore

__all__ = ["create_app"]

# Multipart overhead on top of the per-file upload cap.
_MAX_UPLOAD_BYTES = MAX_UPLOAD_BYTES + 1024 * 1024
# JSON bodies (run triggers with inline support threads, mapping reports).
_MAX_JSON_BYTES = 64 * 1024 * 1024

# Same-origin static UI with no inline scripts; nothing may frame the app.
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


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
        referenced_model_versions,
    )

    prune_runs(
        store,
        max_runs=settings.RUN_RETENTION_MAX,
        pending_ttl_days=settings.PENDING_RUN_TTL_DAYS,
    )
    prune_model_artifacts(
        settings.MODEL_DIR,
        settings.MODEL_RETENTION_MAX,
        protected=referenced_model_versions(store),
    )
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

    try:
        from logging_setup import configure_logging

        configure_logging()
    except Exception as exc:  # noqa: BLE001 - logging must never block serving
        print(f"WARNING: logging configuration failed: {exc}", file=sys.stderr)

    @app.middleware("http")
    async def _log_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Log request metadata only — never bodies, headers, or secrets (D-H4)."""
        bind_request_context()
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            clear_run_context()
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 3)
        get_logger(node="api").info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        clear_run_context()
        return response

    @app.middleware("http")
    async def _limit_body_size(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Refuse oversized bodies up front (before multipart spooling / JSON parsing)."""
        declared = request.headers.get("content-length")
        limit = _MAX_UPLOAD_BYTES if request.url.path == "/uploads" else _MAX_JSON_BYTES
        if declared is not None and declared.isdigit() and int(declared) > limit:
            return JSONResponse(
                status_code=413, content={"detail": f"request body exceeds {limit} bytes"}
            )
        return await call_next(request)

    @app.middleware("http")
    async def _security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    # Every API route requires the key when API_KEY is set (reads included);
    # /health and the static frontend stay open so the UI can load and ask for it.
    authenticated = [Depends(require_auth)]
    app.include_router(health.router)
    app.include_router(runs.router, dependencies=authenticated)
    app.include_router(models.router, dependencies=authenticated)
    app.include_router(mappings.router, dependencies=authenticated)
    app.include_router(uploads.router, dependencies=authenticated)
    app.include_router(configs.router, dependencies=authenticated)

    _mount_frontend(app, resolved)
    return app


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    """Serve the Horizon static frontend when the directory exists (additive).

    Mounted at ``/`` **after** the API routers so the explicit API paths keep
    precedence. Absent directory -> API-only behaviour unchanged.
    """
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    class RevalidatingStaticFiles(StaticFiles):
        # ES modules are otherwise heuristically cached, so a browser can keep
        # running an old view after a frontend update. "no-cache" still lets it
        # reuse its copy, but only after an ETag revalidation (a cheap 304).
        async def get_response(self, path: str, scope: Any) -> Any:
            response = await super().get_response(path, scope)
            response.headers["Cache-Control"] = "no-cache"
            return response

    frontend_dir = Path(settings.FRONTEND_DIR)
    if not frontend_dir.is_dir():
        return
    app.mount(
        "/", RevalidatingStaticFiles(directory=str(frontend_dir), html=True), name="frontend"
    )

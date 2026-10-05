from __future__ import annotations

import contextlib
import os
import sys
import threading
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from api.deps import key_matches, require_auth
from api.limits import RateLimit
from api.routes import configs, health, mappings, models, runs, uploads
from api.routes.uploads import MAX_UPLOAD_BYTES
from config.settings import Settings, get_settings
from logging_setup import bind_request_context, clear_run_context, get_logger
from orchestration.persistence import RunStore

__all__ = ["create_app"]

_MAX_UPLOAD_BYTES = MAX_UPLOAD_BYTES + 1024 * 1024
_MAX_JSON_BYTES = 64 * 1024 * 1024

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


_BODY_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class _BodyTooLarge(HTTPException):
    def __init__(self, limit: int) -> None:
        super().__init__(status_code=413, detail=f"request body exceeds {limit} bytes")


class BodyGuardMiddleware:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def _reject(self, scope: Scope, send: Send, status_code: int, detail: str) -> None:
        response = JSONResponse(status_code=status_code, content={"detail": detail})
        await response(scope, _empty_receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _BODY_METHODS:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        if not self.settings.API_ENABLE_WRITES:
            await self._reject(scope, send, 403, "writes are disabled (API_ENABLE_WRITES=false)")
            return
        provided = headers.get(self.settings.API_KEY_HEADER.lower())
        if not key_matches(self.settings, provided):
            await self._reject(scope, send, 401, "invalid or missing API key")
            return
        limit = _MAX_UPLOAD_BYTES if scope["path"] == "/uploads" else _MAX_JSON_BYTES
        declared = headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await self._reject(scope, send, 413, f"request body exceeds {limit} bytes")
            return

        received = 0
        started = False

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _BodyTooLarge(limit)
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLarge as exc:
            if started:
                raise
            await self._reject(scope, send, 413, str(exc.detail))


async def _empty_receive() -> Message:
    return {"type": "http.disconnect"}


class RunSlots:
    def __init__(self, capacity: int) -> None:
        self.capacity = capacity
        self._in_use = 0
        self._lock = threading.Lock()

    def try_acquire(self) -> bool:
        with self._lock:
            if self._in_use >= self.capacity:
                return False
            self._in_use += 1
            return True

    def release(self, *_args: Any) -> None:
        with self._lock:
            self._in_use = max(0, self._in_use - 1)

    @property
    def in_use(self) -> int:
        return self._in_use


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        executor: Any = getattr(app.state, "executor", None)
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        owner_lock: Any = getattr(app.state, "owner_lock", None)
        if owner_lock is not None:
            owner_lock.release()


class StoreOwnerLock:
    STALE_S = 120.0
    HEARTBEAT_S = 30.0

    def __init__(self, run_dir: str | Path) -> None:
        self.path = Path(run_dir) / ".api-owner.lock"
        self._stop = threading.Event()
        self.owned = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(3):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age <= self.STALE_S:
                    return False
                self.path.unlink(missing_ok=True)
                continue
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.close(fd)
            self.owned = True
            threading.Thread(
                target=self._heartbeat, name="api-owner-heartbeat", daemon=True
            ).start()
            return True
        return False

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.HEARTBEAT_S):
            with contextlib.suppress(OSError):
                os.utime(self.path)

    def release(self) -> None:
        self._stop.set()
        if self.owned:
            self.path.unlink(missing_ok=True)
            self.owned = False


def _startup_recovery(store: RunStore, settings: Settings) -> None:
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
    resolved = settings or get_settings()
    resolved_store = store or RunStore(resolved.RUN_DIR)
    resolved_executor = executor or ThreadPoolExecutor(
        max_workers=resolved.RUN_MAX_WORKERS, thread_name_prefix="churn-run"
    )
    owner_lock: StoreOwnerLock | None = None
    if run_startup_recovery:
        owner_lock = StoreOwnerLock(resolved.RUN_DIR)
        if owner_lock.acquire():
            _startup_recovery(resolved_store, resolved)
        else:
            get_logger(node="api").warning(
                "startup_recovery_skipped",
                reason="another live API process owns this run store",
            )

    app = FastAPI(title="Churn Survival API", version="0.1.0", lifespan=_lifespan)
    app.state.owner_lock = owner_lock
    app.state.settings = resolved
    app.state.store = resolved_store
    app.state.executor = resolved_executor
    app.state.run_slots = RunSlots(resolved.RUN_MAX_QUEUED)
    app.state.run_trigger_limit = RateLimit(resolved.RUN_TRIGGERS_PER_MINUTE, what="run triggers")
    app.state.upload_limit = RateLimit(resolved.UPLOADS_PER_MINUTE, what="uploads")
    app.add_middleware(BodyGuardMiddleware, settings=resolved)

    try:
        from logging_setup import configure_logging

        configure_logging()
    except Exception as exc:  # noqa: BLE001 - logging must never block serving
        print(f"WARNING: logging configuration failed: {exc}", file=sys.stderr)

    @app.middleware("http")
    async def _log_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
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
    async def _security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    authenticated = [Depends(require_auth)] if resolved.API_REQUIRE_KEY_FOR_READS else []
    app.include_router(health.router)
    app.include_router(runs.router, dependencies=authenticated)
    app.include_router(models.router, dependencies=authenticated)
    app.include_router(mappings.router, dependencies=authenticated)
    app.include_router(uploads.router, dependencies=authenticated)
    app.include_router(configs.router, dependencies=authenticated)

    _mount_frontend(app, resolved)
    return app


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    from pathlib import Path

    from fastapi.staticfiles import StaticFiles

    class RevalidatingStaticFiles(StaticFiles):
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

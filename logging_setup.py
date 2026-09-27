"""Structured logging foundation (architecture §8.8, ROADMAP Task 0.5).

Every log line is machine-parseable JSON carrying at least `event`, `level`,
`timestamp`, and — when produced by a node — `node` plus its version fields
(`mapping_version`, `model_version`, ...). `LOG_LEVEL` comes from settings.
"""

from __future__ import annotations

import logging
import sys
from typing import Any
from uuid import uuid4

import structlog

from config.settings import get_settings

_configured = False


class _DynamicStderr:
    """File-like proxy that resolves ``sys.stderr`` at write time.

    structlog's ``PrintLoggerFactory`` binds its file object at construction, so
    a pytest capture that is later closed would break subsequent logging. The
    proxy always writes to the *current* ``sys.stderr``.
    """

    def write(self, message: str) -> int:
        return sys.stderr.write(message)

    def flush(self) -> None:
        sys.stderr.flush()

    def isatty(self) -> bool:
        return False


def configure_logging(*, force: bool = False) -> None:
    """Configure structlog once per process. Idempotent unless ``force=True``.

    ``LOG_FORMAT=json`` (default) emits one JSON object per line for machines;
    ``LOG_FORMAT=console`` emits a human-readable rendering for local use. Logs
    are written to **stderr**, keeping stdout reserved for command output (so a
    node CLI's printed summary stays byte-identical across runs).
    """
    global _configured
    if _configured and not force:
        return

    settings = get_settings()
    min_level = logging.getLevelName(settings.LOG_LEVEL)
    renderer: Any = (
        structlog.processors.ConsoleRenderer()
        if settings.LOG_FORMAT == "console"
        else structlog.processors.JSONRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(min_level),
        logger_factory=structlog.PrintLoggerFactory(file=_DynamicStderr()),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(node: str | None = None, **version_fields: str) -> Any:
    """Return a bound logger carrying `node` and version fields (e.g. `model_version`)."""
    return structlog.get_logger(node=node, **version_fields)


def bind_run_context(*, run_id: str | None, reference_date: str) -> None:
    """Bind run-scoped fields so every subsequent log line carries them.

    Operational only (D-H3): never a decision input. Cleared by
    :func:`clear_run_context` at the end of a run.
    """
    structlog.contextvars.bind_contextvars(run_id=run_id, reference_date=reference_date)


def bind_request_context(request_id: str | None = None) -> str:
    """Bind a request id (generated when absent); returns the id used."""
    resolved = request_id or uuid4().hex
    structlog.contextvars.bind_contextvars(request_id=resolved)
    return resolved


def clear_run_context() -> None:
    """Clear all bound contextvars (run/request scope)."""
    structlog.contextvars.clear_contextvars()


def emit_node_completion(node: str, **fields: Any) -> None:
    """Best-effort ``node_run_completed`` event for a node CLI entry.

    Forces a (re)configuration so the logger binds the current stderr, then logs
    one summary event. Any failure is swallowed — observability must never break
    a command.
    """
    try:
        configure_logging(force=True)
        get_logger(node=node).info("node_run_completed", **fields)
    except Exception:  # noqa: BLE001 - logging is auxiliary, never fatal
        return

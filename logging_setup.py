from __future__ import annotations

import logging
import sys
from typing import Any
from uuid import uuid4

import structlog

from config.settings import get_settings

_configured = False


class _DynamicStderr:
    def write(self, message: str) -> int:
        return sys.stderr.write(message)

    def flush(self) -> None:
        sys.stderr.flush()

    def isatty(self) -> bool:
        return False


def configure_logging(*, force: bool = False) -> None:
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
    return structlog.get_logger(node=node, **version_fields)


def bind_run_context(*, run_id: str | None, reference_date: str) -> None:
    structlog.contextvars.bind_contextvars(run_id=run_id, reference_date=reference_date)


def bind_request_context(request_id: str | None = None) -> str:
    resolved = request_id or uuid4().hex
    structlog.contextvars.bind_contextvars(request_id=resolved)
    return resolved


def clear_run_context() -> None:
    structlog.contextvars.clear_contextvars()


def emit_node_completion(node: str, **fields: Any) -> None:
    try:
        configure_logging(force=True)
        get_logger(node=node).info("node_run_completed", **fields)
    except Exception as exc:  # noqa: BLE001 - logging is auxiliary, never fatal
        print(f"WARNING: logging failed for {node}: {type(exc).__name__}", file=sys.stderr)

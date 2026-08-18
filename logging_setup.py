"""Structured logging foundation (architecture §8.8, ROADMAP Task 0.5).

Every log line is machine-parseable JSON carrying at least `event`, `level`,
`timestamp`, and — when produced by a node — `node` plus its version fields
(`mapping_version`, `model_version`, ...). `LOG_LEVEL` comes from settings.
"""

from __future__ import annotations

import logging
from typing import Any

import structlog

from config.settings import get_settings

_configured = False


def configure_logging(*, force: bool = False) -> None:
    """Configure structlog once per process. Idempotent unless ``force=True``."""
    global _configured
    if _configured and not force:
        return

    settings = get_settings()
    min_level = logging.getLevelName(settings.LOG_LEVEL)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(min_level),
        cache_logger_on_first_use=True,
    )
    _configured = True


def get_logger(node: str | None = None, **version_fields: str) -> Any:
    """Return a bound logger carrying `node` and version fields (e.g. `model_version`)."""
    return structlog.get_logger(node=node, **version_fields)

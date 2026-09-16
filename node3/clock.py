"""Deterministic Node 3 run timestamp (multi-source addendum §8, QA F-3).

Node 3 never uses wall-clock time. Unless the caller supplies an explicit
``now`` (tests, orchestrators), the run timestamp is derived from the declared
``Node3Config.reference_date`` at midnight UTC — the same rule Node 4 uses for
``ranked_at`` and Node 5 uses for ``generated_at``. This keeps identical inputs
plus identical config versions byte-identical across re-runs.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

from config.models import Node3Config


def run_timestamp(config: Node3Config, now: datetime | None = None) -> datetime:
    """Return the declared run timestamp.

    ``now`` wins when supplied; otherwise midnight UTC of ``config.reference_date``.
    Never ``datetime.now()``.
    """
    if now is not None:
        return now
    return datetime.combine(config.reference_date, time(0, 0), tzinfo=UTC)

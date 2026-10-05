from __future__ import annotations

from datetime import UTC, datetime, time

from config.models import Node3Config


def run_timestamp(config: Node3Config, now: datetime | None = None) -> datetime:
    if now is not None:
        return now
    return datetime.combine(config.reference_date, time(0, 0), tzinfo=UTC)

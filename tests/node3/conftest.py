"""Shared fixtures/factories for Node 3 tests (ROADMAP Task 4.14)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from config.loader import load_node3_config, load_vocabulary
from config.models import Node3Config, VocabularyConfig
from node3.llm_extractor import extract_thread_signals
from node3.preprocess import preprocess_threads
from schemas.node3 import SupportThread, ThreadSignals

REFERENCE_DATE = date(2026, 8, 15)
NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def node3_config() -> Node3Config:
    return load_node3_config("1")


@pytest.fixture
def vocabulary() -> VocabularyConfig:
    return load_vocabulary()


@pytest.fixture
def now() -> datetime:
    return NOW


def message(
    message_id: str,
    text: str,
    *,
    role: str = "customer",
    timestamp: str = "2026-08-01T00:00:00Z",
) -> dict[str, Any]:
    return {"message_id": message_id, "role": role, "text": text, "timestamp": timestamp}


def thread(
    thread_id: str = "THR-1",
    customer_id: str = "CUST-1",
    *,
    created_at: str = "2026-08-01T00:00:00Z",
    subject: str | None = "Help",
    channel: str | None = "email",
    messages: list[dict[str, Any]] | None = None,
    language: str | None = None,
    duplicate_of: str | None = None,
) -> dict[str, Any]:
    if messages is None:
        messages = [message(f"{thread_id}-m1", "I want to cancel my subscription.")]
    return {
        "thread_id": thread_id,
        "customer_id": customer_id,
        "created_at": created_at,
        "closed_at": None,
        "channel": channel,
        "subject": subject,
        "status": None,
        "tags": None,
        "language": language,
        "duplicate_of": duplicate_of,
        "messages": messages,
    }


def extract_signals(
    thread_dicts: list[dict[str, Any]],
    config: Node3Config,
    *,
    now: datetime = NOW,
) -> tuple[list[ThreadSignals], list[bool]]:
    """Preprocess + offline-extract a batch; return (signals, failed flags)."""
    valid = [SupportThread.model_validate(t) for t in thread_dicts]
    items, _ = preprocess_threads(valid, config)
    outcomes = [extract_thread_signals(item, config, now=now) for item in items]
    return [o.signals for o in outcomes], [o.failed for o in outcomes]


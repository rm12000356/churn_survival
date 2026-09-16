from __future__ import annotations

from config.models import Node3Config
from node3.preprocess import preprocess_threads
from schemas.enums import LanguageStatus
from tests.node3.conftest import message, thread


def _ids(items) -> list[str]:
    return [item.thread.thread_id for item in items]


def test_future_leakage_dropped(node3_config: Node3Config) -> None:
    future = thread("T1", created_at="2026-09-01T00:00:00Z")
    ok = thread("T2", created_at="2026-08-01T00:00:00Z")
    items, stats = preprocess_threads([future, ok], node3_config)
    assert _ids(items) == ["T2"]
    assert stats.n_out_of_window == 1


def test_lookback_window_dropped(node3_config: Node3Config) -> None:
    old = thread("T1", created_at="2024-01-01T00:00:00Z")
    recent = thread("T2", created_at="2026-08-01T00:00:00Z")
    items, stats = preprocess_threads([old, recent], node3_config)
    assert _ids(items) == ["T2"]
    assert stats.n_out_of_window == 1


def test_system_messages_removed(node3_config: Node3Config) -> None:
    t = thread(
        "T1",
        messages=[
            message("m1", "auto reply", role="system"),
            message("m2", "I want to cancel my subscription.", role="customer"),
        ],
    )
    items, stats = preprocess_threads([t], node3_config)
    assert stats.n_system_messages_removed == 1
    assert [m.message_id for m in items[0].thread.messages] == ["m2"]


def test_duplicate_customer_messages_removed(node3_config: Node3Config) -> None:
    t = thread(
        "T1",
        messages=[
            message("m1", "The export is missing rows."),
            message("m2", "the   export is missing rows.", role="customer"),
            message("m3", "Still missing.", role="agent"),
        ],
    )
    items, stats = preprocess_threads([t], node3_config)
    assert stats.n_duplicate_messages_removed == 1
    assert [m.message_id for m in items[0].thread.messages] == ["m1", "m3"]


def test_truncate_to_max_messages(node3_config: Node3Config) -> None:
    config = node3_config.model_copy(update={"max_messages_per_thread": 2})
    t = thread(
        "T1",
        messages=[
            message(
                f"m{i}",
                f"message number {i} about billing",
                timestamp=f"2026-08-01T0{i}:00:00Z",
            )
            for i in range(4)
        ],
    )
    items, _ = preprocess_threads([t], config)
    assert [m.message_id for m in items[0].thread.messages] == ["m0", "m1"]


def test_customer_thread_limit_keeps_most_recent(node3_config: Node3Config) -> None:
    config = node3_config.model_copy(update={"max_threads_per_customer": 1})
    older = thread("T1", created_at="2026-01-01T00:00:00Z")
    newer = thread("T2", created_at="2026-08-01T00:00:00Z")
    items, stats = preprocess_threads([older, newer], config)
    assert _ids(items) == ["T2"]
    assert stats.n_threads_over_limit == 1


def test_missing_customer_id_dropped(node3_config: Node3Config) -> None:
    bad = thread("T1", customer_id="   ")
    items, stats = preprocess_threads([bad], node3_config)
    assert items == []
    assert stats.n_dropped_invalid == 1
    assert stats.errors[0]["code"] == "MISSING_CUSTOMER_ID"


def test_invalid_thread_dict_dropped(node3_config: Node3Config) -> None:
    items, stats = preprocess_threads([{"thread_id": "T1"}], node3_config)
    assert items == []
    assert stats.n_dropped_invalid == 1
    assert stats.errors[0]["code"] == "INVALID_THREAD"


def test_provided_language_honored(node3_config: Node3Config) -> None:
    t = thread("T1", language="es", messages=[message("m1", "hola necesito ayuda")])
    items, stats = preprocess_threads([t], node3_config)
    assert items[0].language == "es"
    assert items[0].language_status is LanguageStatus.UNSUPPORTED
    assert stats.n_unsupported_language == 1

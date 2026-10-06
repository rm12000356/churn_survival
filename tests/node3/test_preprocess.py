from __future__ import annotations

from config.models import Node3Config
from node3.preprocess import _customer_token_count, preprocess_threads
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


def test_thread_window_uses_utc_for_aware_created_at(node3_config: Node3Config) -> None:
    # 2026-08-15T23:00-10:00 is 2026-08-16 in UTC → outside the window.
    future = thread("T1", created_at="2026-08-15T23:00:00-10:00")
    # 2026-08-16T01:00+14:00 is still 2026-08-15 in UTC → inside the window.
    ok = thread("T2", created_at="2026-08-16T01:00:00+14:00")
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


def test_future_message_removed(node3_config: Node3Config) -> None:
    t = thread(
        "T1",
        created_at="2026-08-01T00:00:00Z",
        messages=[
            message("m1", "I want to cancel.", timestamp="2026-08-10T00:00:00Z"),
            message("m2", "Still cancelling.", timestamp="2026-08-20T00:00:00Z"),
        ],
    )
    items, stats = preprocess_threads([t], node3_config)
    assert [m.message_id for m in items[0].thread.messages] == ["m1"]
    assert stats.n_future_messages_removed == 1


def test_message_on_reference_date_kept(node3_config: Node3Config) -> None:
    t = thread(
        "T1",
        messages=[message("m1", "cancel now", timestamp="2026-08-15T23:59:59Z")],
    )
    items, stats = preprocess_threads([t], node3_config)
    assert [m.message_id for m in items[0].thread.messages] == ["m1"]
    assert stats.n_future_messages_removed == 0


def test_thread_with_only_future_messages_kept_but_empty(node3_config: Node3Config) -> None:
    t = thread(
        "T1",
        created_at="2026-08-01T00:00:00Z",
        messages=[message("m1", "future", timestamp="2026-09-01T00:00:00Z")],
    )
    items, stats = preprocess_threads([t], node3_config)
    assert [item.thread.thread_id for item in items] == ["T1"]
    assert items[0].thread.messages == []
    assert stats.n_future_messages_removed == 1


def test_future_message_comparison_uses_utc_for_aware_timestamps(
    node3_config: Node3Config,
) -> None:
    t = thread(
        "T1",
        created_at="2026-08-01T00:00:00Z",
        messages=[
            message("keep", "cancel", timestamp="2026-08-16T01:00:00+14:00"),
            message("drop", "cancel", timestamp="2026-08-15T23:00:00-10:00"),
        ],
    )
    items, stats = preprocess_threads([t], node3_config)
    assert [m.message_id for m in items[0].thread.messages] == ["keep"]
    assert stats.n_future_messages_removed == 1


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


def test_token_budget_drops_oversized_thread(node3_config: Node3Config) -> None:
    config = node3_config.model_copy(update={"max_tokens_per_customer": 1})
    t = thread("T1", messages=[message("m1", " ".join(["word"] * 500))])
    items, stats = preprocess_threads([t], config)
    assert items == []
    assert stats.n_threads_over_token_budget == 1
    errors = [e for e in stats.errors if e["code"] == "TOKEN_BUDGET_EXCEEDED"]
    assert len(errors) == 1
    assert errors[0]["thread_ids"] == ["T1"]


def test_token_budget_keeps_newest_threads(node3_config: Node3Config) -> None:
    config = node3_config.model_copy(update={"max_tokens_per_customer": 5})
    older = thread(
        "T1",
        created_at="2026-08-01T00:00:00Z",
        subject="Alpha issue",
        messages=[message("m1", "alpha beta gamma delta")],
    )
    newer = thread(
        "T2",
        created_at="2026-08-04T00:00:00Z",
        subject="Omega issue",
        messages=[message("m2", "one two three four")],
    )
    items, stats = preprocess_threads([older, newer], config)
    assert [i.thread.thread_id for i in items] == ["T2"]
    assert stats.n_threads_over_token_budget == 1
    errors = [e for e in stats.errors if e["code"] == "TOKEN_BUDGET_EXCEEDED"]
    assert len(errors) == 1
    assert errors[0]["thread_ids"] == ["T1"]


def test_token_budget_keeps_all_when_within(node3_config: Node3Config) -> None:
    config = node3_config.model_copy(update={"max_tokens_per_customer": 1000})
    threads = [
        thread(
            "T1",
            created_at="2026-08-01T00:00:00Z",
            subject="Alpha issue",
            messages=[message("m1", "alpha beta gamma delta")],
        ),
        thread(
            "T2",
            created_at="2026-08-04T00:00:00Z",
            subject="Omega issue",
            messages=[message("m2", "one two three four")],
        ),
    ]
    items, stats = preprocess_threads(threads, config)
    assert {i.thread.thread_id for i in items} == {"T1", "T2"}
    assert stats.n_threads_over_token_budget == 0


def test_token_budget_retains_unsupported_threads(node3_config: Node3Config) -> None:
    config = node3_config.model_copy(update={"max_tokens_per_customer": 1})
    unsupported = thread(
        "T1", language="es", subject="Ayuda", messages=[message("m1", "una dos tres")]
    )
    oversized = thread(
        "T2",
        created_at="2026-08-04T00:00:00Z",
        subject="Omega issue",
        messages=[message("m2", " ".join(["x"] * 50))],
    )
    items, stats = preprocess_threads([unsupported, oversized], config)
    ids = {i.thread.thread_id for i in items}
    assert "T1" in ids
    assert "T2" not in ids
    assert stats.n_threads_over_token_budget == 1


def test_token_budget_is_deterministic(node3_config: Node3Config) -> None:
    config = node3_config.model_copy(update={"max_tokens_per_customer": 5})
    threads = [
        thread(
            "T1",
            created_at="2026-08-01T00:00:00Z",
            subject="Alpha issue",
            messages=[message("m1", "alpha beta gamma delta")],
        ),
        thread(
            "T2",
            created_at="2026-08-04T00:00:00Z",
            subject="Omega issue",
            messages=[message("m2", "one two three four")],
        ),
    ]
    first, _ = preprocess_threads(threads, config)
    second, _ = preprocess_threads(threads, config)
    assert [i.thread.thread_id for i in first] == [i.thread.thread_id for i in second]


def _three_distinct_threads() -> list[dict]:
    return [
        thread(
            "T1",
            created_at="2026-08-01T00:00:00Z",
            subject="Alpha issue",
            messages=[message("m1", "alpha beta")],
        ),
        thread(
            "T2",
            created_at="2026-08-02T00:00:00Z",
            subject="Beta issue",
            messages=[message("m2", "gamma delta")],
        ),
        thread(
            "T3",
            created_at="2026-08-03T00:00:00Z",
            subject="Gamma issue",
            messages=[message("m3", "epsilon zeta")],
        ),
    ]


def test_thread_limit_and_token_budget_order(node3_config: Node3Config) -> None:
    # Order: duplicate collapse -> max_threads_per_customer -> max_tokens budget.
    config = node3_config.model_copy(
        update={"max_threads_per_customer": 2, "max_tokens_per_customer": 1000}
    )
    items, stats = preprocess_threads(_three_distinct_threads(), config)
    assert {i.thread.thread_id for i in items} == {"T2", "T3"}
    assert stats.n_duplicates_collapsed == 0
    assert stats.n_threads_over_limit == 1  # T1 dropped by the thread cap first
    assert stats.n_threads_over_token_budget == 0


def test_token_budget_operates_after_thread_limit(node3_config: Node3Config) -> None:
    # max_threads keeps {T3,T2}; budget=3 then drops T2 (newest-first, whole thread).
    config = node3_config.model_copy(
        update={"max_threads_per_customer": 2, "max_tokens_per_customer": 3}
    )
    items, stats = preprocess_threads(_three_distinct_threads(), config)
    assert {i.thread.thread_id for i in items} == {"T3"}
    assert stats.n_threads_over_limit == 1
    assert stats.n_threads_over_token_budget == 1
    errors = [e for e in stats.errors if e["code"] == "TOKEN_BUDGET_EXCEEDED"]
    assert errors[0]["thread_ids"] == ["T2"]
    # No retained thread bypasses the hard budget.
    retained = [i for i in items if i.duplicate_of is None]
    assert sum(_customer_token_count(i.thread) for i in retained) <= config.max_tokens_per_customer


def test_token_budget_excludes_collapsed_and_unsupported(node3_config: Node3Config) -> None:
    # Collapse first: the collapsed duplicate's tokens must not count. If they did,
    # 6 + 6 > 6 would drop the survivor.
    config = node3_config.model_copy(update={"max_tokens_per_customer": 6})
    shared = "My invoice shows the wrong amount."
    survivor = thread(
        "S",
        created_at="2026-08-01T00:00:00Z",
        subject="Billing question",
        messages=[message("s1", shared)],
    )
    duplicate = thread(
        "D",
        created_at="2026-08-01T06:00:00Z",
        subject="Billing question",
        duplicate_of="S",
        messages=[message("d1", shared)],
    )
    unsupported = thread(
        "U",
        created_at="2026-08-02T00:00:00Z",
        language="es",
        subject="Ayuda",
        messages=[message("u1", " ".join(["palabra"] * 50))],
    )
    items, stats = preprocess_threads([survivor, duplicate, unsupported], config)
    by_id = {i.thread.thread_id: i for i in items}
    assert stats.n_duplicates_collapsed == 1
    assert stats.n_threads_over_token_budget == 0
    assert by_id["D"].duplicate_of == "S"
    assert by_id["S"].duplicate_of is None
    assert by_id["U"].duplicate_of is None  # unsupported retained, never budgeted


def test_thread_limit_and_token_budget_deterministic(node3_config: Node3Config) -> None:
    config = node3_config.model_copy(
        update={"max_threads_per_customer": 2, "max_tokens_per_customer": 3}
    )
    first, _ = preprocess_threads(_three_distinct_threads(), config)
    second, _ = preprocess_threads(_three_distinct_threads(), config)
    assert [i.thread.thread_id for i in first] == [i.thread.thread_id for i in second]

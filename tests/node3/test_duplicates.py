from __future__ import annotations

from config.models import Node3Config
from node3.preprocess import _pick_survivor, preprocess_threads
from schemas.node3 import SupportThread
from tests.node3.conftest import message, thread


def test_valid_duplicate_hint_collapsed(node3_config: Node3Config) -> None:
    survivor = thread(
        "T1",
        subject="Billing question",
        created_at="2026-08-01T00:00:00Z",
        messages=[message("m1", "My invoice shows the wrong amount.")],
    )
    duplicate = thread(
        "T2",
        subject="Billing question",
        created_at="2026-08-01T06:00:00Z",
        duplicate_of="T1",
        messages=[message("m2", "My invoice shows the wrong amount!")],
    )
    items, stats = preprocess_threads([survivor, duplicate], node3_config)
    by_id = {item.thread.thread_id: item for item in items}
    assert by_id["T2"].duplicate_of == "T1"
    assert by_id["T1"].duplicate_of is None
    assert stats.n_duplicates_collapsed == 1


def test_invalid_duplicate_hint_ignored(node3_config: Node3Config) -> None:
    t1 = thread(
        "T1",
        subject="Access",
        created_at="2026-04-20T00:00:00Z",
        messages=[message("m1", "I cannot access my account.")],
    )
    t2 = thread(
        "T2",
        subject="Support request",
        created_at="2026-05-03T12:00:00Z",
        duplicate_of="T1",
        messages=[message("m2", "Please help.")],
    )
    items, stats = preprocess_threads([t1, t2], node3_config)
    by_id = {item.thread.thread_id: item for item in items}
    assert by_id["T2"].duplicate_of is None
    assert stats.n_duplicates_collapsed == 0


def test_hint_over_48h_ignored(node3_config: Node3Config) -> None:
    # Identical content (would pass cosine/subject) but outside the 48h window.
    t1 = thread(
        "T1",
        subject="Billing question",
        created_at="2026-08-01T00:00:00Z",
        messages=[message("m1", "My invoice shows the wrong amount.")],
    )
    t2 = thread(
        "T2",
        subject="Billing question",
        created_at="2026-08-05T00:00:00Z",
        duplicate_of="T1",
        messages=[message("m2", "My invoice shows the wrong amount.")],
    )
    items, stats = preprocess_threads([t1, t2], node3_config)
    assert stats.n_duplicates_collapsed == 0


def test_hint_nonexistent_target_ignored(node3_config: Node3Config) -> None:
    t = thread(
        "T1",
        subject="Billing question",
        duplicate_of="THR-DOES-NOT-EXIST",
        messages=[message("m1", "My invoice shows the wrong amount.")],
    )
    items, stats = preprocess_threads([t], node3_config)
    assert items[0].duplicate_of is None
    assert stats.n_duplicates_collapsed == 0


def test_hint_chain_terminates(node3_config: Node3Config) -> None:
    shared = "My invoice shows the wrong amount."
    t1 = thread(
        "T1",
        created_at="2026-08-01T00:00:00Z",
        duplicate_of="T2",
        messages=[message("m1", shared)],
    )
    t2 = thread(
        "T2",
        created_at="2026-08-01T06:00:00Z",
        duplicate_of="T3",
        messages=[message("m2", shared), message("m2b", "please help me")],
    )
    t3 = thread(
        "T3",
        created_at="2026-08-01T12:00:00Z",
        messages=[
            message("m3", shared),
            message("m3b", "please help me"),
            message("m3c", "this is urgent now"),
        ],
    )
    items, stats = preprocess_threads([t1, t2, t3], node3_config)
    by_id = {item.thread.thread_id: item for item in items}
    assert stats.n_duplicates_collapsed == 2
    assert by_id["T1"].duplicate_of == "T2"
    assert by_id["T2"].duplicate_of == "T3"
    assert by_id["T3"].duplicate_of is None


def test_hint_cycle_terminates(node3_config: Node3Config) -> None:
    shared = "My invoice shows the wrong amount."
    t1 = thread(
        "T1",
        created_at="2026-08-01T00:00:00Z",
        duplicate_of="T2",
        messages=[message("m1", shared)],
    )
    t2 = thread(
        "T2",
        created_at="2026-08-01T06:00:00Z",
        duplicate_of="T1",
        messages=[
            message("m2", shared),
            message("m2b", "please help me"),
            message("m2c", "this is urgent now"),
        ],
    )
    items, stats = preprocess_threads([t1, t2], node3_config)
    by_id = {item.thread.thread_id: item for item in items}
    assert stats.n_duplicates_collapsed == 1
    survivors = [tid for tid, item in by_id.items() if item.duplicate_of is None]
    assert survivors == ["T2"]  # more tokens wins, regardless of hint direction
    assert by_id["T1"].duplicate_of == "T2"


def test_hint_direction_does_not_decide_survivor(node3_config: Node3Config) -> None:
    # LONG points at SHORT, but LONG has more customer tokens -> LONG survives.
    shared = "My invoice shows the wrong amount."
    short = thread(
        "SHORT",
        subject="Billing question",
        created_at="2026-08-01T00:00:00Z",
        messages=[message("s1", shared)],
    )
    long = thread(
        "LONG",
        subject="Billing question",
        created_at="2026-08-01T06:00:00Z",
        duplicate_of="SHORT",
        messages=[message("l1", shared), message("l2", "please fix it soon")],
    )
    items, stats = preprocess_threads([short, long], node3_config)
    by_id = {item.thread.thread_id: item for item in items}
    assert stats.n_duplicates_collapsed == 1
    assert by_id["LONG"].duplicate_of is None
    assert by_id["SHORT"].duplicate_of == "LONG"


def test_hint_reverse_direction_same_survivor(node3_config: Node3Config) -> None:
    # SHORT points at LONG; LONG still has more tokens and must still survive.
    shared = "My invoice shows the wrong amount."
    short = thread(
        "SHORT",
        subject="Billing question",
        created_at="2026-08-01T00:00:00Z",
        duplicate_of="LONG",
        messages=[message("s1", shared)],
    )
    long = thread(
        "LONG",
        subject="Billing question",
        created_at="2026-08-01T06:00:00Z",
        messages=[message("l1", shared), message("l2", "please fix it soon")],
    )
    items, stats = preprocess_threads([short, long], node3_config)
    by_id = {item.thread.thread_id: item for item in items}
    assert stats.n_duplicates_collapsed == 1
    assert by_id["LONG"].duplicate_of is None
    assert by_id["SHORT"].duplicate_of == "LONG"


def test_spec_based_near_duplicate_collapsed(node3_config: Node3Config) -> None:
    a = thread(
        "T1",
        subject="Billing question",
        created_at="2026-08-01T00:00:00Z",
        messages=[message("m1", "My invoice shows the wrong amount.")],
    )
    b = thread(
        "T2",
        subject="Billing question",
        created_at="2026-08-01T06:00:00Z",
        messages=[message("m2", "My invoice shows the wrong amount!")],
    )
    items, stats = preprocess_threads([a, b], node3_config)
    assert stats.n_duplicates_collapsed == 1
    collapsed = next(item for item in items if item.duplicate_of)
    assert collapsed.duplicate_of == "T1"


def test_dissimilar_threads_not_collapsed(node3_config: Node3Config) -> None:
    a = thread(
        "T1", subject="Billing", messages=[message("m1", "My invoice shows the wrong amount.")]
    )
    b = thread("T2", subject="Feature", messages=[message("m2", "Please add SSO support.")])
    _, stats = preprocess_threads([a, b], node3_config)
    assert stats.n_duplicates_collapsed == 0


def test_near_exact_punctuation_message_collapsed(node3_config: Node3Config) -> None:
    t = thread(
        "T1",
        subject="Plan",
        messages=[
            message("m1", "I want to cancel my plan."),
            message("m2", "I want to cancel my plan!", timestamp="2026-08-01T00:01:00Z"),
        ],
    )
    items, stats = preprocess_threads([t], node3_config)
    assert stats.n_duplicate_messages_removed == 1
    assert [m.message_id for m in items[0].thread.messages] == ["m1"]


def test_near_exact_negation_not_collapsed(node3_config: Node3Config) -> None:
    t = thread(
        "T1",
        subject="Plan",
        messages=[
            message("m1", "I want to cancel my plan."),
            message("m2", "I don't want to cancel my plan!", timestamp="2026-08-01T00:01:00Z"),
        ],
    )
    items, stats = preprocess_threads([t], node3_config)
    assert stats.n_duplicate_messages_removed == 0
    assert len(items[0].thread.messages) == 2


def test_pick_survivor_prefers_more_customer_tokens(node3_config: Node3Config) -> None:
    short = SupportThread.model_validate(thread("T1", messages=[message("m1", "cancel")]))
    long = SupportThread.model_validate(
        thread("T2", messages=[message("m2", "I want to cancel my subscription today please")])
    )
    survivor, loser = _pick_survivor(short, long)
    assert survivor.thread_id == "T2"
    assert loser.thread_id == "T1"


def test_pick_survivor_ties_break_on_earlier_then_id() -> None:
    early = SupportThread.model_validate(
        thread("T2", created_at="2026-08-01T00:00:00Z", messages=[message("m1", "hello there")])
    )
    late = SupportThread.model_validate(
        thread("T1", created_at="2026-08-02T00:00:00Z", messages=[message("m2", "hello there")])
    )
    assert _pick_survivor(late, early)[0].thread_id == "T2"

from __future__ import annotations

from config.models import Node3Config
from node3.preprocess import _pick_survivor, preprocess_threads
from schemas.node3 import SupportThread
from tests.node3.conftest import message, thread


def test_explicit_duplicate_hint_collapsed(node3_config: Node3Config) -> None:
    survivor = thread(
        "T1", subject="Access", messages=[message("m1", "I cannot access my account.")]
    )
    duplicate = thread(
        "T2",
        subject="Support request",
        created_at="2026-05-03T12:00:00Z",
        duplicate_of="T1",
        messages=[message("m2", "Please help.")],
    )
    items, stats = preprocess_threads([survivor, duplicate], node3_config)
    by_id = {item.thread.thread_id: item for item in items}
    assert by_id["T2"].duplicate_of == "T1"
    assert by_id["T1"].duplicate_of is None
    assert stats.n_duplicates_collapsed == 1


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

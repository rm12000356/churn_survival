from __future__ import annotations

from config.models import Node3Config, VocabularyConfig
from node3.aggregate import aggregate_customer
from schemas.enums import FlagType
from tests.node3.conftest import NOW, extract_signals, message, thread


def test_recurrence_and_temporal_span(
    node3_config: Node3Config, vocabulary: VocabularyConfig
) -> None:
    threads = [
        thread(
            "T1",
            created_at="2026-06-01T00:00:00Z",
            messages=[
                message(
                    "m1",
                    "I want to cancel my subscription.",
                    timestamp="2026-06-01T00:00:00Z",
                )
            ],
        ),
        thread(
            "T2",
            created_at="2026-08-01T00:00:00Z",
            messages=[
                message(
                    "m2",
                    "I want to cancel my subscription.",
                    timestamp="2026-08-01T00:00:00Z",
                )
            ],
        ),
    ]
    signals, _ = extract_signals(threads, node3_config)
    result = aggregate_customer(
        "CUST-1", signals, node3_config, vocabulary=vocabulary, now=NOW
    )
    flag = result.risk_flags[0]
    assert flag.flag_type is FlagType.CANCELLATION_INTENT
    assert flag.recurrence_count == 2
    assert flag.first_observed_at < flag.last_observed_at
    assert set(flag.evidence_message_ids) == {"m1", "m2"}


def test_strongest_instance_confidence(
    node3_config: Node3Config, vocabulary: VocabularyConfig
) -> None:
    threads = [
        thread(
            "T1",
            created_at="2026-08-14T00:00:00Z",
            messages=[message("m1", "I want to cancel my subscription.")],
        ),
        thread(
            "T2",
            created_at="2026-01-01T00:00:00Z",
            messages=[message("m2", "I want to cancel my subscription.")],
        ),
    ]
    signals, _ = extract_signals(threads, node3_config)
    result = aggregate_customer(
        "CUST-1", signals, node3_config, vocabulary=vocabulary, now=NOW
    )
    # most recent instance is the strongest under recency decay
    assert result.risk_flags[0].strongest_evidence.message_id == "m1"


def test_collapsed_duplicates_excluded_from_counts(
    node3_config: Node3Config, vocabulary: VocabularyConfig
) -> None:
    threads = [
        thread(
            "T1",
            created_at="2026-08-01T00:00:00Z",
            messages=[message("m1", "I want to cancel my subscription.")],
        ),
        thread(
            "T2",
            created_at="2026-08-01T00:00:00Z",
            duplicate_of="T1",
            messages=[message("m2", "I want to cancel my subscription.")],
        ),
    ]
    signals, _ = extract_signals(threads, node3_config)
    result = aggregate_customer(
        "CUST-1", signals, node3_config, vocabulary=vocabulary, now=NOW
    )
    assert result.n_threads_in_window == 1
    assert result.risk_flags[0].recurrence_count == 1


def test_no_flags_gives_none_strength(
    node3_config: Node3Config, vocabulary: VocabularyConfig
) -> None:
    threads = [
        thread(
            "T1",
            messages=[message("m1", "Can you reset my password?")],
        )
    ]
    signals, _ = extract_signals(threads, node3_config)
    result = aggregate_customer(
        "CUST-1", signals, node3_config, vocabulary=vocabulary, now=NOW
    )
    assert result.signal_strength.value == "none"
    assert result.risk_flags == []
    assert result.summary is None


def test_latest_interaction_at_uses_last_message(
    node3_config: Node3Config, vocabulary: VocabularyConfig
) -> None:
    threads = [
        thread(
            "T1",
            created_at="2026-08-12T18:19:00Z",
            messages=[
                message("m1", "Can you help?", timestamp="2026-08-12T18:19:00Z"),
                message(
                    "m2",
                    "We will help you.",
                    role="agent",
                    timestamp="2026-08-12T23:37:00Z",
                ),
            ],
        )
    ]
    signals, _ = extract_signals(threads, node3_config)
    result = aggregate_customer(
        "CUST-1", signals, node3_config, vocabulary=vocabulary, now=NOW
    )
    assert result.latest_interaction_at is not None
    assert result.latest_interaction_at.isoformat().startswith("2026-08-12T23:37:00")


def test_latest_interaction_at_none_without_threads(
    node3_config: Node3Config, vocabulary: VocabularyConfig
) -> None:
    result = aggregate_customer(
        "CUST-1", [], node3_config, vocabulary=vocabulary, now=NOW
    )
    assert result.latest_interaction_at is None


def test_n_messages_in_window_excludes_system_and_duplicates(
    node3_config: Node3Config, vocabulary: VocabularyConfig
) -> None:
    threads = [
        thread(
            "T1",
            messages=[
                message("m1", "Hello there friend", timestamp="2026-08-01T00:00:00Z"),
                message("m2", "Hello there friend!", timestamp="2026-08-01T00:01:00Z"),
                message("sys", "auto reply", role="system"),
                message(
                    "m3",
                    "We are here to help",
                    role="agent",
                    timestamp="2026-08-01T00:02:00Z",
                ),
            ],
        )
    ]
    signals, _ = extract_signals(threads, node3_config)
    result = aggregate_customer(
        "CUST-1", signals, node3_config, vocabulary=vocabulary, now=NOW
    )
    assert result.n_messages_in_window == 2

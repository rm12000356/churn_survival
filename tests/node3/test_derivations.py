from __future__ import annotations

from config.models import Node3Config, VocabularyConfig
from node3.aggregate import aggregate_customer
from schemas.enums import FlagType, SentimentLabel
from tests.node3.conftest import NOW, extract_signals, message, thread


def _aggregate(threads, config, vocab):
    signals, _ = extract_signals(threads, config)
    return aggregate_customer("CUST-1", signals, config, vocabulary=vocab, now=NOW)


def test_sentiment_positive(node3_config: Node3Config, vocabulary: VocabularyConfig) -> None:
    result = _aggregate(
        [thread("T1", messages=[message("m1", "Love the product, great job!")])],
        node3_config,
        vocabulary,
    )
    assert result.overall_sentiment.label is SentimentLabel.POSITIVE


def test_sentiment_negative(node3_config: Node3Config, vocabulary: VocabularyConfig) -> None:
    result = _aggregate(
        [thread("T1", messages=[message("m1", "I want to cancel my subscription.")])],
        node3_config,
        vocabulary,
    )
    assert result.overall_sentiment.label is SentimentLabel.NEGATIVE


def test_urgency_takes_max(node3_config: Node3Config, vocabulary: VocabularyConfig) -> None:
    threads = [
        thread(
            "T1",
            created_at="2026-08-01T00:00:00Z",
            messages=[message("m1", "URGENT: the system is down!")],
        ),
        thread(
            "T2",
            created_at="2026-08-02T00:00:00Z",
            messages=[message("m2", "Can you reset my password?")],
        ),
    ]
    result = _aggregate(threads, node3_config, vocabulary)
    assert result.urgency_level.value == "high"


def test_escalation_from_high_urgency(
    node3_config: Node3Config, vocabulary: VocabularyConfig
) -> None:
    result = _aggregate(
        [thread("T1", messages=[message("m1", "This is urgent, please fix ASAP.")])],
        node3_config,
        vocabulary,
    )
    assert result.escalation_signal is True


def test_escalation_from_high_severity_recurrence(
    node3_config: Node3Config, vocabulary: VocabularyConfig
) -> None:
    threads = [
        thread(
            "T1",
            created_at="2026-06-01T00:00:00Z",
            messages=[message("m1", "I want to cancel my subscription.")],
        ),
        thread(
            "T2",
            created_at="2026-08-01T00:00:00Z",
            messages=[message("m2", "I want to cancel my subscription.")],
        ),
    ]
    result = _aggregate(threads, node3_config, vocabulary)
    assert result.escalation_signal is True


def test_churn_language_or(node3_config: Node3Config, vocabulary: VocabularyConfig) -> None:
    threads = [
        thread(
            "T1",
            created_at="2026-08-01T00:00:00Z",
            messages=[message("m1", "Can you reset my password?")],
        ),
        thread(
            "T2",
            created_at="2026-08-02T00:00:00Z",
            messages=[message("m2", "I am not renewing.")],
        ),
    ]
    result = _aggregate(threads, node3_config, vocabulary)
    assert result.churn_language_detected is True


def test_signal_strength_hierarchy_first(
    node3_config: Node3Config, vocabulary: VocabularyConfig
) -> None:
    threads = [
        thread(
            "T1",
            created_at="2026-08-01T00:00:00Z",
            messages=[message("m1", "My invoice shows the wrong amount.")],
        ),
        thread(
            "T2",
            created_at="2026-08-02T00:00:00Z",
            messages=[message("m2", "I want to cancel my subscription.")],
        ),
    ]
    result = _aggregate(threads, node3_config, vocabulary)
    assert result.risk_flags[0].flag_type is FlagType.CANCELLATION_INTENT
    assert result.signal_strength.value == "strong"


def test_support_data_statuses(node3_config: Node3Config, vocabulary: VocabularyConfig) -> None:
    no_data = _aggregate([], node3_config, vocabulary)
    assert no_data.support_data_status.value == "no_data"

    limited = _aggregate(
        [thread("T1", messages=[message("m1", "help")])], node3_config, vocabulary
    )
    assert limited.support_data_status.value == "limited_data"

    sufficient_threads = [
        thread(
            "T1",
            created_at="2026-08-01T00:00:00Z",
            messages=[
                message("m1", "I want to cancel my subscription."),
                message("m2", "I need help with billing."),
                message("m3", "Please respond soon."),
            ],
        )
    ]
    sufficient = _aggregate(sufficient_threads, node3_config, vocabulary)
    assert sufficient.support_data_status.value == "sufficient_data"

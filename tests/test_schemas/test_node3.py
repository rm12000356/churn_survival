from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.node3 import (
    CustomerSupportSignals,
    Node3Output,
    SupportThread,
    ThreadSignals,
)
from tests.conftest import make_support_thread

THREAD_SIGNALS = {
    "thread_id": "thr_1001",
    "customer_id": "cus_8f3a2b1c",
    "created_at": "2026-07-20T14:30:00Z",
    "language": "en",
    "language_status": "supported",
    "duplicate_of": None,
    "sentiment": {"label": "negative", "score": -0.4, "confidence": 0.9},
    "risk_flags": [
        {
            "flag_type": "cancellation_intent",
            "severity": "high",
            "signal_strength": "strong",
            "confidence": 0.92,
            "evidence": {
                "message_id": "msg_1001_1",
                "text": "We are considering cancelling before the renewal.",
                "timestamp": "2026-07-20T14:30:00Z",
            },
            "evidence_message_ids": ["msg_1001_1"],
        }
    ],
    "churn_language_detected": True,
    "urgency_level": "high",
    "key_themes": ["renewal", "pricing"],
    "meta": {
        "n_customer_messages": 1,
        "n_agent_messages": 1,
        "n_tokens_sent": 120,
        "processed_at": "2026-08-17T10:00:00Z",
        "prompt_version": "prompt_v1",
        "model_version": "model_v1",
    },
}

CUSTOMER_SIGNALS = {
    "customer_id": "cus_8f3a2b1c",
    "support_data_status": "sufficient_data",
    "has_support_data": True,
    "n_threads_in_window": 3,
    "n_messages_in_window": 9,
    "latest_interaction_at": "2026-08-01T09:00:00Z",
    "overall_sentiment": {"label": "negative", "score": -0.3, "confidence": 0.8},
    "risk_flags": [
        {
            "flag_type": "cancellation_intent",
            "severity": "high",
            "signal_strength": "strong",
            "confidence": 0.85,
            "recurrence_count": 2,
            "first_observed_at": "2026-07-20T14:30:00Z",
            "last_observed_at": "2026-08-01T09:00:00Z",
            "strongest_evidence": {
                "message_id": "msg_1001_1",
                "text": "We are considering cancelling before the renewal.",
                "timestamp": "2026-07-20T14:30:00Z",
            },
            "evidence_message_ids": ["msg_1001_1", "msg_1002_1"],
        }
    ],
    "signal_strength": "strong",
    "overall_signal_confidence": 0.78,
    "key_themes": ["renewal", "pricing"],
    "urgency_level": "high",
    "escalation_signal": True,
    "churn_language_detected": True,
    "summary": None,
    "meta": {
        "lookback_days": 365,
        "processed_at": "2026-08-17T10:00:00Z",
        "prompt_version": "prompt_v1",
        "model_version": "model_v1",
        "aggregation_version": "agg_v1",
        "vocabulary_version": "vocab_v1",
        "preprocessing_version": "pre_v1",
    },
}


def test_support_thread_round_trip() -> None:
    thread = SupportThread.model_validate(make_support_thread())
    assert thread.thread_id == "thr_1001"
    assert thread.messages[0].role == "customer"
    assert thread.messages[0].timestamp.isoformat().startswith("2026-07-20T14:30:00")


def test_support_thread_invalid_role_rejected() -> None:
    thread = make_support_thread()
    thread["messages"][0]["role"] = "billing"
    with pytest.raises(ValidationError):
        SupportThread.model_validate(thread)


def test_thread_signals_round_trip() -> None:
    signals = ThreadSignals.model_validate(THREAD_SIGNALS)
    assert signals.risk_flags[0].flag_type.value == "cancellation_intent"
    assert signals.urgency_level.value == "high"
    dumped = signals.model_dump(mode="json")
    assert dumped["risk_flags"][0]["flag_type"] == "cancellation_intent"


def test_thread_signals_invalid_flag_type_rejected() -> None:
    flag = {**THREAD_SIGNALS["risk_flags"][0], "flag_type": "hated_us"}
    payload = {**THREAD_SIGNALS, "risk_flags": [flag]}
    with pytest.raises(ValidationError):
        ThreadSignals.model_validate(payload)


def test_thread_signals_invalid_sentiment_rejected() -> None:
    sentiment = {"label": "furious", "score": -1.0, "confidence": 0.5}
    payload = {**THREAD_SIGNALS, "sentiment": sentiment}
    with pytest.raises(ValidationError):
        ThreadSignals.model_validate(payload)


def test_key_themes_are_open() -> None:
    payload = {**THREAD_SIGNALS, "key_themes": ["anything", "goes", "here"]}
    ThreadSignals.model_validate(payload)


def test_customer_signals_round_trip() -> None:
    signals = CustomerSupportSignals.model_validate(CUSTOMER_SIGNALS)
    assert signals.risk_flags[0].recurrence_count == 2
    assert signals.support_data_status.value == "sufficient_data"


def test_customer_signals_zero_recurrence_rejected() -> None:
    flag = {**CUSTOMER_SIGNALS["risk_flags"][0], "recurrence_count": 0}
    payload = {**CUSTOMER_SIGNALS, "risk_flags": [flag]}
    with pytest.raises(ValidationError):
        CustomerSupportSignals.model_validate(payload)


def test_customer_signals_no_data_state() -> None:
    no_data = {
        "customer_id": "cus_new",
        "support_data_status": "no_data",
        "has_support_data": False,
        "n_threads_in_window": 0,
        "n_messages_in_window": 0,
        "latest_interaction_at": None,
        "overall_sentiment": {"label": "unknown", "score": None, "confidence": 0.0},
        "risk_flags": [],
        "signal_strength": "none",
        "overall_signal_confidence": 0.0,
        "key_themes": [],
        "urgency_level": "unknown",
        "escalation_signal": False,
        "churn_language_detected": False,
        "summary": None,
        "meta": CUSTOMER_SIGNALS["meta"],
    }
    signals = CustomerSupportSignals.model_validate(no_data)
    assert signals.overall_signal_confidence == 0.0


def test_node3_output_round_trip() -> None:
    output = Node3Output.model_validate(
        {
            "customer_signals": [CUSTOMER_SIGNALS],
            "thread_signals": [THREAD_SIGNALS],
            "processing_report": {
                "n_customers_requested": 1,
                "n_customers_with_data": 1,
                "n_customers_with_signals": 1,
                "n_threads_processed": 1,
                "n_threads_failed": 0,
                "n_cross_channel_duplicates_collapsed": 0,
                "llm_calls": 1,
                "warnings": [],
                "errors": [],
            },
        }
    )
    assert len(output.customer_signals) == 1
    assert output.processing_report.llm_calls == 1

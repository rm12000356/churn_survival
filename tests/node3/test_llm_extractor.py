from __future__ import annotations

import json
import os

import pytest

from config.models import Node3Config
from node3.llm_extractor import extract_thread_signals
from node3.preprocess import preprocess_threads
from schemas.enums import LanguageStatus
from schemas.node3 import SupportThread
from tests.node3.conftest import NOW, message, thread


class MockClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.model = "mock-model"
        self.calls = 0
        self.temperatures: list[float] = []

    def complete(self, prompt: str, *, temperature: float = 0.2) -> str:
        self.calls += 1
        self.temperatures.append(temperature)
        return self.responses.pop(0)


def _item(thread_dict: dict, config: Node3Config):
    valid = SupportThread.model_validate(thread_dict)
    items, _ = preprocess_threads([valid], config)
    return items[0]


_PAYLOAD = {
    "sentiment": {"label": "negative", "score": -0.5, "confidence": 0.9},
    "risk_flags": [
        {
            "flag_type": "cancellation_intent",
            "severity": "high",
            "signal_strength": "strong",
            "confidence": 0.9,
            "message_id": "T1-m1",
        }
    ],
    "churn_language_detected": True,
    "urgency_level": "high",
    "key_themes": ["cancellation_intent"],
}


def test_offline_extraction(node3_config: Node3Config) -> None:
    outcome = extract_thread_signals(
        _item(thread("T1"), node3_config), node3_config, now=NOW
    )
    assert outcome.failed is False
    assert outcome.llm_called is False
    assert outcome.signals.risk_flags[0].flag_type.value == "cancellation_intent"
    assert outcome.signals.meta.model_version == "offline"


def test_unsupported_language_quarantined(node3_config: Node3Config) -> None:
    item = _item(thread("T1", language="es"), node3_config)
    outcome = extract_thread_signals(item, node3_config, now=NOW)
    assert outcome.failed is True
    assert outcome.llm_called is False
    assert outcome.error is not None and outcome.error["code"] == "UNSUPPORTED_LANGUAGE"
    assert outcome.signals.language_status is LanguageStatus.UNSUPPORTED


def test_only_agent_messages_no_llm(node3_config: Node3Config) -> None:
    t = thread(
        "T1",
        messages=[message("m1", "We will help you.", role="agent")],
    )
    outcome = extract_thread_signals(_item(t, node3_config), node3_config, now=NOW)
    assert outcome.failed is False
    assert outcome.signals.risk_flags == []
    assert outcome.signals.meta.n_customer_messages == 0


def test_llm_success(node3_config: Node3Config) -> None:
    client = MockClient([json.dumps(_PAYLOAD)])
    outcome = extract_thread_signals(
        _item(thread("T1"), node3_config), node3_config, client=client, now=NOW
    )
    assert outcome.llm_called is True
    assert outcome.failed is False
    assert outcome.signals.risk_flags[0].evidence.message_id == "T1-m1"
    assert outcome.signals.meta.model_version == "mock-model"
    assert client.calls == 1


def test_llm_uses_configured_temperature(node3_config: Node3Config) -> None:
    config = node3_config.model_copy(update={"llm_temperature": 0.05})
    client = MockClient([json.dumps(_PAYLOAD)])
    extract_thread_signals(
        _item(thread("T1"), config), config, client=client, now=NOW
    )
    assert client.temperatures == [0.05]


def test_llm_retry_then_quarantine(node3_config: Node3Config) -> None:
    client = MockClient(["not json", "still not json"])
    outcome = extract_thread_signals(
        _item(thread("T1"), node3_config), node3_config, client=client, now=NOW
    )
    assert outcome.llm_called is True
    assert outcome.failed is True
    assert client.calls == 2
    assert outcome.error is not None and outcome.error["code"] == "LLM_EXTRACTION_FAILED"


def test_llm_unknown_message_id_quarantined(node3_config: Node3Config) -> None:
    bad = json.loads(json.dumps(_PAYLOAD))
    bad["risk_flags"][0]["message_id"] = "does-not-exist"
    client = MockClient([json.dumps(bad), json.dumps(bad)])
    outcome = extract_thread_signals(
        _item(thread("T1"), node3_config), node3_config, client=client, now=NOW
    )
    assert outcome.failed is True


def test_recovery_after_first_invalid(node3_config: Node3Config) -> None:
    client = MockClient(["{ broken", json.dumps(_PAYLOAD)])
    outcome = extract_thread_signals(
        _item(thread("T1"), node3_config), node3_config, client=client, now=NOW
    )
    assert outcome.failed is False
    assert client.calls == 2


def _flags(node3_config: Node3Config, text: str) -> set[str]:
    t = thread("T1", messages=[message("m1", text)])
    outcome = extract_thread_signals(_item(t, node3_config), node3_config, now=NOW)
    return {flag.flag_type.value for flag in outcome.signals.risk_flags}


def test_offline_billing_precision(node3_config: Node3Config) -> None:
    assert "billing_complaint" not in _flags(node3_config, "Can you update my billing email?")
    assert "billing_complaint" in _flags(
        node3_config, "My invoice shows the wrong amount."
    )


def test_offline_feature_precision(node3_config: Node3Config) -> None:
    assert "feature_missing" not in _flags(node3_config, "The export is missing rows.")
    assert "feature_missing" in _flags(
        node3_config, "The feature I paid for is missing."
    )


def test_n_tokens_sent_counts_customer_messages(node3_config: Node3Config) -> None:
    t = thread(
        "T1",
        messages=[
            message("m1", "cancel my plan now"),
            message("m2", " ".join(["word"] * 50), role="agent"),
        ],
    )
    outcome = extract_thread_signals(_item(t, node3_config), node3_config, now=NOW)
    assert outcome.signals.meta.n_tokens_sent == 4


@pytest.mark.llm
@pytest.mark.skipif(
    not os.environ.get("RUN_LLM_TESTS"), reason="live LLM tests disabled (set RUN_LLM_TESTS=1)"
)
def test_live_llm_extraction(node3_config: Node3Config) -> None:
    from router.llm_mapper import create_llm_client

    outcome = extract_thread_signals(
        _item(thread("T1"), node3_config), node3_config, client=create_llm_client(), now=NOW
    )
    assert outcome.llm_called is True
    assert outcome.failed is False

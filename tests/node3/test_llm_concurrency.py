"""REVIEW §5 — bounded concurrent LLM extraction is output-identical to sequential."""

from __future__ import annotations

import json
import re
import threading
import time

from config.models import Node3Config
from node3.node import run_node3
from tests.node3.conftest import NOW, message, thread

_MESSAGE_ID = re.compile(r'<untrusted_message id="([^"]+)">')


class ThreadSafeClient:
    """LLM double: per-prompt deterministic payload; records peak concurrency."""

    def __init__(self, delay: float = 0.0) -> None:
        self.model = "mock-model"
        self.delay = delay
        self.calls = 0
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()

    def complete(self, prompt: str, *, temperature: float = 0.2) -> str:
        with self._lock:
            self.calls += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            message_id = _MESSAGE_ID.search(prompt).group(1)  # type: ignore[union-attr]
            cancel = "cancel" in prompt.lower()
            return json.dumps(
                {
                    "sentiment": {"label": "negative", "score": -0.5, "confidence": 0.9},
                    "risk_flags": [
                        {
                            "flag_type": (
                                "cancellation_intent" if cancel else "billing_complaint"
                            ),
                            "severity": "high",
                            "signal_strength": "strong",
                            "confidence": 0.9,
                            "message_id": message_id,
                        }
                    ],
                    "churn_language_detected": cancel,
                    "urgency_level": "high",
                    "key_themes": [],
                }
            )
        finally:
            with self._lock:
                self.active -= 1


def _threads(n: int) -> list[dict]:
    out = []
    for index in range(n):
        text = (
            "I want to cancel my subscription." if index % 2 else "My invoice is wrong."
        )
        day = f"2026-08-{index % 9 + 1:02d}T00:00:00Z"
        out.append(
            thread(
                f"THR-{index}",
                f"CUST-{index % 5}",
                created_at=day,
                messages=[message(f"THR-{index}-m1", f"{text} ticket {index}", timestamp=day)],
            )
        )
    return out


def _run(config: Node3Config, client: ThreadSafeClient) -> str:
    output = run_node3(
        [f"CUST-{i}" for i in range(5)], _threads(20), config, llm_client=client, now=NOW
    )
    return output.model_dump_json()


def test_concurrent_extraction_matches_sequential(node3_config: Node3Config) -> None:
    sequential = node3_config.model_copy(update={"llm_max_concurrency": 1})
    concurrent = node3_config.model_copy(update={"llm_max_concurrency": 8})
    seq_client = ThreadSafeClient()
    con_client = ThreadSafeClient(delay=0.01)

    assert _run(sequential, seq_client) == _run(concurrent, con_client)
    assert seq_client.calls == con_client.calls > 1  # dedup collapses some threads
    assert seq_client.peak == 1
    assert con_client.peak > 1


def test_concurrency_is_bounded(node3_config: Node3Config) -> None:
    config = node3_config.model_copy(update={"llm_max_concurrency": 3})
    client = ThreadSafeClient(delay=0.01)
    _run(config, client)
    assert 1 < client.peak <= 3

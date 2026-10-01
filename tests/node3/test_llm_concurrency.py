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


class FlakyClient(ThreadSafeClient):
    """Fails the provider call for prompts naming a listed thread; garbles others."""

    def __init__(self, *, down: set[str], garbled: set[str] = frozenset(), delay=0.0) -> None:
        super().__init__(delay=delay)
        self.down = down
        self.garbled = garbled

    def complete(self, prompt: str, *, temperature: float = 0.2) -> str:
        message_id = _MESSAGE_ID.search(prompt).group(1)  # type: ignore[union-attr]
        thread_id = message_id.rsplit("-m1", 1)[0]
        if thread_id in self.down:
            with self._lock:
                self.calls += 1
            raise ConnectionError("provider down")
        if thread_id in self.garbled:
            with self._lock:
                self.calls += 1
            return "not json"
        return super().complete(prompt, temperature=temperature)


def _codes(output) -> list[str]:
    return [e["code"] for e in output.processing_report.errors]


def test_total_outage_is_a_run_level_warning_and_trips_breaker(
    node3_config: Node3Config,
) -> None:
    # REVIEW N-H5: an expired key must not look like a clean COMPLETED run.
    config = node3_config.model_copy(
        update={"llm_max_concurrency": 1, "llm_max_retries": 0, "llm_max_consecutive_failures": 3}
    )
    client = FlakyClient(down={f"THR-{i}" for i in range(20)})
    output = run_node3(
        [f"CUST-{i}" for i in range(5)], _threads(20), config, llm_client=client, now=NOW
    )
    report = output.processing_report
    assert client.calls == 3  # the breaker stopped calling after 3 failures
    assert any("failed LLM extraction" in w for w in report.warnings)
    assert any("LLM circuit opened after 3" in w for w in report.warnings)
    assert "LLM_CIRCUIT_OPEN" in _codes(output)
    # Only collapsed duplicates (offline, never sent) remain processed.
    assert report.n_threads_processed == report.n_cross_channel_duplicates_collapsed


def test_breaker_output_is_identical_at_any_concurrency(node3_config: Node3Config) -> None:
    # REVIEW T4/T5 analogue for Node 3: the trip point is decided in input order.
    down = {"THR-3", "THR-4", "THR-5", "THR-6", "THR-7", "THR-8", "THR-9"}
    base = node3_config.model_copy(
        update={"llm_max_retries": 0, "llm_max_consecutive_failures": 3}
    )
    runs = []
    for concurrency in (1, 4, 8):
        config = base.model_copy(update={"llm_max_concurrency": concurrency})
        output = run_node3(
            [f"CUST-{i}" for i in range(5)],
            _threads(20),
            config,
            llm_client=FlakyClient(down=down, delay=0.002),
            now=NOW,
        )
        runs.append(output.model_dump_json())
    assert runs[0] == runs[1] == runs[2]
    assert "LLM_CIRCUIT_OPEN" in runs[0]


def test_mixed_failures_match_sequential(node3_config: Node3Config) -> None:
    # REVIEW T5: provider failures and garbled payloads interleaved, breaker never trips.
    down = {"THR-2", "THR-9"}
    garbled = {"THR-4", "THR-11"}
    base = node3_config.model_copy(update={"llm_max_retries": 0})
    outputs = []
    for concurrency in (1, 8):
        config = base.model_copy(update={"llm_max_concurrency": concurrency})
        outputs.append(
            run_node3(
                [f"CUST-{i}" for i in range(5)],
                _threads(20),
                config,
                llm_client=FlakyClient(down=down, garbled=garbled, delay=0.002),
                now=NOW,
            )
        )
    assert outputs[0].model_dump_json() == outputs[1].model_dump_json()
    errors = outputs[0].processing_report.errors
    failed = {e["thread_id"] for e in errors if e["code"] == "LLM_EXTRACTION_FAILED"}
    assert failed and failed <= down | garbled
    assert "LLM_CIRCUIT_OPEN" not in _codes(outputs[0])


def test_garbled_payloads_do_not_trip_the_breaker(node3_config: Node3Config) -> None:
    # Only provider failures count: a model answering badly is not an outage.
    config = node3_config.model_copy(
        update={"llm_max_concurrency": 1, "llm_max_retries": 0, "llm_max_consecutive_failures": 2}
    )
    client = FlakyClient(down=set(), garbled={f"THR-{i}" for i in range(20)})
    output = run_node3(
        [f"CUST-{i}" for i in range(5)], _threads(20), config, llm_client=client, now=NOW
    )
    assert "LLM_CIRCUIT_OPEN" not in _codes(output)
    assert not any("circuit opened" in w for w in output.processing_report.warnings)

"""Shared fixtures/builders for Node 5 tests (ROADMAP Phase 6)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from config.loader import load_action_rules, load_node4_config, load_node5_config
from config.models import ActionRulesConfig, Node5Config
from node4.node import run_node4
from schemas.enums import (
    FlagType,
    Severity,
    SignalStrength,
    UrgencyLevel,
)
from schemas.node3 import (
    Evidence,
    RiskFlag,
    Sentiment,
    ThreadSignals,
    ThreadSignalsMeta,
)
from schemas.node4 import Node4Output
from tests.node4.conftest import make_association, make_flag, make_node2, make_node3, make_signal

REFERENCE_DATE = date(2026, 8, 15)
NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)


class FakeLlmClient:
    """Deterministic in-memory LLM double (never calls a real provider)."""

    def __init__(self, responses: list[object]) -> None:
        self.model = "fake-model"
        self.responses = list(responses)
        self.calls = 0

    def complete(self, prompt: str, *, temperature: float = 0.2) -> str:
        self.calls += 1
        response = self.responses[min(self.calls - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return str(response)


@pytest.fixture(scope="session")
def node5_config() -> Node5Config:
    return load_node5_config("1")


@pytest.fixture(scope="session")
def node5_dataset7_config() -> Node5Config:
    return load_node5_config("dataset7")


@pytest.fixture(scope="session")
def action_rules() -> ActionRulesConfig:
    return load_action_rules("1")


def make_thread_signal(
    thread_id: str,
    customer_id: str,
    *,
    flag_type: str = "cancellation_intent",
    severity: str = "high",
    signal_strength: str = "strong",
    message_id: str | None = None,
    text: str = "We are considering cancelling before the renewal.",
    timestamp: datetime = NOW,
    include_flag: bool = True,
) -> ThreadSignals:
    """Thread-level signal carrying real evidence for the Node 3 lookup index."""
    mid = message_id or f"msg_{flag_type}"
    flags: list[RiskFlag] = []
    if include_flag:
        flags.append(
            RiskFlag(
                flag_type=FlagType(flag_type),
                severity=Severity(severity),
                signal_strength=SignalStrength(signal_strength),
                confidence=0.9,
                evidence=Evidence(message_id=mid, text=text, timestamp=timestamp),
                evidence_message_ids=[mid],
            )
        )
    return ThreadSignals(
        thread_id=thread_id,
        customer_id=customer_id,
        created_at=timestamp,
        latest_message_at=timestamp,
        language="en",
        language_status="supported",
        sentiment=Sentiment(label="negative", score=-0.5, confidence=0.8),
        risk_flags=flags,
        churn_language_detected=include_flag,
        urgency_level=UrgencyLevel.HIGH if include_flag else UrgencyLevel.LOW,
        key_themes=[],
        meta=ThreadSignalsMeta(
            n_customer_messages=1,
            n_agent_messages=0,
            n_tokens_sent=5,
            processed_at=timestamp,
            prompt_version="prompt_v1.0",
            model_version="offline",
        ),
    )


def make_sample_inputs() -> tuple[Node4Output, Any]:
    """A mixed Node 4 output plus the Node 3 output used to build it."""
    config = load_node4_config("1")
    node2 = make_node2(
        ["A", "B", "C", "D", "E"],
        states=["scored", "scored", "scored", "scored", "not_enough_data"],
        risk_scores=[0.9, 0.8, 0.45, 0.1],
        survival_90=[0.1, 0.15, 0.55, 0.95],
        feature_associations=[
            make_association("usage_frequency", coefficient=-0.4, hazard_ratio=0.67),
            make_association("contract_length_months", coefficient=-0.2, hazard_ratio=0.82),
        ],
    )
    node3 = make_node3(
        [
            make_signal(
                "A",
                risk_flags=[make_flag("cancellation_intent", signal_strength="strong")],
                churn_language_detected=True,
            ),
            make_signal(
                "B",
                risk_flags=[make_flag("billing_complaint", signal_strength="strong")],
            ),
            make_signal(
                "C",
                risk_flags=[make_flag("poor_support_experience", signal_strength="moderate")],
            ),
            make_signal(
                "D",
                risk_flags=[make_flag("positive_feedback", signal_strength="strong")],
            ),
        ],
        thread_signals=[
            make_thread_signal("thr_A", "A", flag_type="cancellation_intent"),
            make_thread_signal(
                "thr_B",
                "B",
                flag_type="billing_complaint",
                text="My invoice was charged twice this month.",
            ),
        ],
    )
    return run_node4(node2, node3, config), node3


def make_sample_node4() -> Node4Output:
    """A mixed Node 4 output: critical / high / medium / low / insufficient."""
    node4, _ = make_sample_inputs()
    return node4

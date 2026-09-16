"""Shared fixtures/builders for Node 4 tests (ROADMAP Tasks 5.1–5.12)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from config.loader import load_node4_config
from config.models import Node4Config
from schemas.enums import (
    CustomerState,
    FlagType,
    HorizonStatus,
    ModelStatus,
    ModelType,
    Severity,
    SignalStrength,
    SupportDataStatus,
    UrgencyLevel,
)
from schemas.node2 import FeatureAssociation, HorizonResult, Node2Output
from schemas.node3 import (
    AggregatedRiskFlag,
    CustomerSupportSignals,
    CustomerSupportSignalsMeta,
    Evidence,
    Node3Output,
    Node3ProcessingReport,
    Sentiment,
    ThreadSignals,
    ThreadSignalsMeta,
)

REFERENCE_DATE = date(2026, 8, 15)
NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="session")
def node4_config() -> Node4Config:
    return load_node4_config("1")


def make_flag(
    flag_type: str,
    *,
    severity: str = "high",
    signal_strength: str = "strong",
    recurrence_count: int = 1,
    confidence: float = 0.9,
    message_id: str | None = None,
) -> AggregatedRiskFlag:
    mid = message_id or f"msg_{flag_type}"
    return AggregatedRiskFlag(
        flag_type=FlagType(flag_type),
        severity=Severity(severity),
        signal_strength=SignalStrength(signal_strength),
        confidence=confidence,
        recurrence_count=recurrence_count,
        first_observed_at=NOW,
        last_observed_at=NOW,
        strongest_evidence=Evidence(message_id=mid, text="evidence text", timestamp=NOW),
        evidence_message_ids=[mid],
    )


def make_signal_meta(**overrides: Any) -> CustomerSupportSignalsMeta:
    base: dict[str, Any] = {
        "lookback_days": 365,
        "processed_at": NOW,
        "prompt_version": "prompt_v1.0",
        "model_version": "offline",
        "aggregation_version": "agg_v1.0",
        "vocabulary_version": "vocab_v1.0",
        "preprocessing_version": "pre_v1.0",
        "reference_date": REFERENCE_DATE,
    }
    base.update(overrides)
    return CustomerSupportSignalsMeta(**base)


def make_signal(
    customer_id: str,
    *,
    support_data_status: str = "sufficient_data",
    signal_strength: str = "none",
    overall_signal_confidence: float = 0.8,
    risk_flags: list[AggregatedRiskFlag] | None = None,
    churn_language_detected: bool = False,
    escalation_signal: bool = False,
    urgency_level: str = "low",
    n_threads_in_window: int = 2,
    meta: CustomerSupportSignalsMeta | None = None,
) -> CustomerSupportSignals:
    flags = risk_flags or []
    return CustomerSupportSignals(
        customer_id=customer_id,
        support_data_status=SupportDataStatus(support_data_status),
        has_support_data=support_data_status != "no_data",
        n_threads_in_window=n_threads_in_window,
        n_messages_in_window=n_threads_in_window * 2,
        latest_interaction_at=NOW if support_data_status != "no_data" else None,
        overall_sentiment=Sentiment(label="negative", score=-0.5, confidence=0.8),
        risk_flags=flags,
        signal_strength=signal_strength,
        overall_signal_confidence=overall_signal_confidence,
        key_themes=[],
        urgency_level=UrgencyLevel(urgency_level),
        escalation_signal=escalation_signal,
        churn_language_detected=churn_language_detected,
        summary=None,
        meta=meta or make_signal_meta(),
    )


def make_thread(
    thread_id: str,
    customer_id: str,
) -> ThreadSignals:
    return ThreadSignals(
        thread_id=thread_id,
        customer_id=customer_id,
        created_at=NOW,
        latest_message_at=NOW,
        language="en",
        language_status="supported",
        sentiment=Sentiment(label="negative", score=-0.5, confidence=0.8),
        risk_flags=[],
        churn_language_detected=False,
        urgency_level=UrgencyLevel.LOW,
        key_themes=[],
        meta=ThreadSignalsMeta(
            n_customer_messages=1,
            n_agent_messages=0,
            n_tokens_sent=5,
            processed_at=NOW,
            prompt_version="prompt_v1.0",
            model_version="offline",
        ),
    )


def make_node3(
    signals: list[CustomerSupportSignals] | None = None,
    thread_signals: list[ThreadSignals] | None = None,
) -> Node3Output:
    signals = signals or []
    return Node3Output(
        customer_signals=signals,
        thread_signals=thread_signals or [],
        processing_report=Node3ProcessingReport(
            n_customers_requested=len(signals),
            n_customers_with_data=sum(1 for s in signals if s.has_support_data),
            n_customers_with_signals=sum(
                1 for s in signals if s.risk_flags or s.churn_language_detected
            ),
            n_threads_processed=len(thread_signals or []),
            n_threads_failed=0,
            n_cross_channel_duplicates_collapsed=0,
            llm_calls=0,
        ),
    )


def make_node2(
    customer_ids: list[str],
    *,
    states: list[str] | None = None,
    risk_scores: list[float] | None = None,
    survival_90: list[float] | None = None,
    horizon_status: str = "AVAILABLE",
    model_type: str = "cox_ph",
    model_status: str = "READY",
    model_version: str = "mv_test",
    feature_associations: list[dict[str, Any]] | None = None,
) -> Node2Output:
    state_values = states or ["scored"] * len(customer_ids)
    associations = (
        [
            item if isinstance(item, FeatureAssociation) else FeatureAssociation(**item)
            for item in feature_associations
        ]
        if feature_associations is not None
        else None
    )
    horizon = HorizonResult(
        status=HorizonStatus(horizon_status),
        values=survival_90,
    )
    return Node2Output(
        model_type=ModelType(model_type),
        model_status=ModelStatus(model_status),
        model_version=model_version,
        risk_scores=risk_scores,
        survival_probabilities={"90d": horizon},
        feature_associations=associations,
        customer_ids=customer_ids,
        customer_states=[CustomerState(state) for state in state_values],
    )


def make_association(
    feature: str,
    *,
    coefficient: float,
    hazard_ratio: float,
) -> FeatureAssociation:
    return FeatureAssociation(
        feature=feature,
        coefficient=coefficient,
        hazard_ratio=hazard_ratio,
        ci_lower=hazard_ratio * 0.8,
        ci_upper=hazard_ratio * 1.2,
        p_value=0.01,
        interpretation="test",
    )

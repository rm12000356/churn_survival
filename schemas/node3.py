"""Node 3 contracts (architecture §3.2/§3.4/§3.5/§3.11, ROADMAP Task 1.1).

Thread-level and customer-level support signals, plus the full Node 3 output.
`key_themes` is an open list; everything else is strict.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.enums import (
    FlagType,
    LanguageStatus,
    OverallSignalStrength,
    SentimentLabel,
    Severity,
    SignalStrength,
    SupportDataStatus,
    UrgencyLevel,
)

MessageRole = Literal["customer", "agent", "system"]


class SupportMessage(BaseModel):
    """One message inside a support thread (§3.2)."""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    timestamp: datetime
    role: MessageRole
    text: str


class SupportThread(BaseModel):
    """Input support thread (§3.2)."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    customer_id: str
    created_at: datetime
    closed_at: datetime | None = None
    channel: str | None = None
    subject: str | None = None
    status: str | None = None
    tags: list[str] | None = None
    messages: list[SupportMessage]


class Sentiment(BaseModel):
    """Sentiment at thread or customer level (§3.4/§3.5)."""

    model_config = ConfigDict(extra="forbid")

    label: SentimentLabel
    score: float | None = Field(default=None, ge=-1, le=1)
    confidence: float = Field(..., ge=0, le=1)


class Evidence(BaseModel):
    """An evidence pointer (message_id, text, timestamp) (§3.4)."""

    model_config = ConfigDict(extra="forbid")

    message_id: str
    text: str
    timestamp: datetime


class RiskFlag(BaseModel):
    """Thread-level risk flag (§3.4)."""

    model_config = ConfigDict(extra="forbid")

    flag_type: FlagType
    severity: Severity
    signal_strength: SignalStrength
    confidence: float = Field(..., ge=0, le=1)
    evidence: Evidence
    evidence_message_ids: list[str] = Field(default_factory=list)


class ThreadSignalsMeta(BaseModel):
    """Thread-level extraction metadata (§3.4)."""

    model_config = ConfigDict(extra="forbid")

    n_customer_messages: int = Field(..., ge=0)
    n_agent_messages: int = Field(..., ge=0)
    n_tokens_sent: int = Field(..., ge=0)
    processed_at: datetime
    prompt_version: str
    model_version: str


class ThreadSignals(BaseModel):
    """Thread-level extraction output (§3.4)."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    customer_id: str
    created_at: datetime
    language: str | None = None
    language_status: LanguageStatus
    duplicate_of: str | None = None
    sentiment: Sentiment
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    churn_language_detected: bool = False
    urgency_level: UrgencyLevel
    key_themes: list[str] = Field(default_factory=list)
    meta: ThreadSignalsMeta


class AggregatedRiskFlag(BaseModel):
    """Customer-level aggregated flag (§3.5)."""

    model_config = ConfigDict(extra="forbid")

    flag_type: FlagType
    severity: Severity
    signal_strength: SignalStrength
    confidence: float = Field(..., ge=0, le=1)
    recurrence_count: int = Field(..., ge=1)
    first_observed_at: datetime
    last_observed_at: datetime
    strongest_evidence: Evidence
    evidence_message_ids: list[str] = Field(default_factory=list)


class CustomerSupportSignalsMeta(BaseModel):
    """Customer-level aggregation metadata (§3.5)."""

    model_config = ConfigDict(extra="forbid")

    lookback_days: int = Field(..., ge=0)
    processed_at: datetime
    prompt_version: str
    model_version: str
    aggregation_version: str
    vocabulary_version: str
    preprocessing_version: str


class CustomerSupportSignals(BaseModel):
    """Customer-level support signals (§3.5)."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str
    support_data_status: SupportDataStatus
    has_support_data: bool
    n_threads_in_window: int = Field(..., ge=0)
    n_messages_in_window: int = Field(..., ge=0)
    latest_interaction_at: datetime | None = None
    overall_sentiment: Sentiment
    risk_flags: list[AggregatedRiskFlag] = Field(default_factory=list)
    signal_strength: OverallSignalStrength
    overall_signal_confidence: float = Field(..., ge=0, le=1)
    key_themes: list[str] = Field(default_factory=list)
    urgency_level: UrgencyLevel
    escalation_signal: bool = False
    churn_language_detected: bool = False
    summary: str | None = None
    meta: CustomerSupportSignalsMeta


class Node3ProcessingReport(BaseModel):
    """Node 3 processing report (§3.11)."""

    model_config = ConfigDict(extra="forbid")

    n_customers_requested: int = Field(..., ge=0)
    n_customers_with_data: int = Field(..., ge=0)
    n_customers_with_signals: int = Field(..., ge=0)
    n_threads_processed: int = Field(..., ge=0)
    n_threads_failed: int = Field(..., ge=0)
    n_cross_channel_duplicates_collapsed: int = Field(..., ge=0)
    llm_calls: int = Field(..., ge=0)
    warnings: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class Node3Output(BaseModel):
    """Full Node 3 output (§3.11)."""

    model_config = ConfigDict(extra="forbid")

    customer_signals: list[CustomerSupportSignals] = Field(default_factory=list)
    thread_signals: list[ThreadSignals] = Field(default_factory=list)
    processing_report: Node3ProcessingReport

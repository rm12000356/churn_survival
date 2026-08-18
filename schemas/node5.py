"""Node 5 contracts (architecture §5.9/§5.10/§5.11/§5.26, ROADMAP Task 1.1).

Client-facing report schemas, evidence representation, metadata, and the full
Node 5 output. `evidence` references are never fabricated; quotes come only from
Node 3 evidence.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from schemas.enums import (
    CustomerState,
    FlagType,
    OverallSignalStrength,
    ReasonType,
    ReportRiskLevel,
    ReportSeverity,
    Severity,
    SignalStrength,
    SupportDataStatus,
)
from schemas.node4 import ReasonSource


class ReportReason(BaseModel):
    """A reason as presented in the client report (§5.10)."""

    model_config = ConfigDict(extra="forbid")

    reason_type: ReasonType
    source: ReasonSource
    severity: ReportSeverity
    statement: str
    evidence_ref: str | dict[str, Any]


class ReportFlag(BaseModel):
    """A top flag as presented in the client report (§5.9)."""

    model_config = ConfigDict(extra="forbid")

    flag_type: FlagType
    severity: Severity
    signal_strength: SignalStrength


class Node2ReportReference(BaseModel):
    """Node 2 evidence reference in a report (§5.11)."""

    model_config = ConfigDict(extra="forbid")

    model_version: str
    feature_ref: str | None = None


class Node3ReportReference(BaseModel):
    """Node 3 evidence reference in a report (§5.11)."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    message_id: str
    timestamp: datetime
    evidence_text: str


class ReportEvidence(BaseModel):
    """Evidence representation (§5.11). Presentation may shorten it; reference persists."""

    model_config = ConfigDict(extra="forbid")

    source: ReasonSource
    description: str
    node2_reference: Node2ReportReference | None = None
    node3_reference: Node3ReportReference | None = None


class QuantitativeSummary(BaseModel):
    """Quantitative summary inside a customer report (§5.9)."""

    model_config = ConfigDict(extra="forbid")

    risk_score: float | None = Field(default=None, ge=0, le=1)
    survival_prob_90d: float | None = Field(default=None, ge=0, le=1)
    top_drivers: list[str] = Field(default_factory=list)
    customer_state: CustomerState


class SupportSummary(BaseModel):
    """Support summary inside a customer report (§5.9)."""

    model_config = ConfigDict(extra="forbid")

    support_data_status: SupportDataStatus
    signal_strength: OverallSignalStrength
    churn_language_detected: bool = False
    escalation_signal: bool = False
    top_flags: list[ReportFlag] = Field(default_factory=list)


class CustomerReport(BaseModel):
    """One customer entry in the client report (§5.9)."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str
    display_name: str
    rank: int = Field(..., ge=1)
    risk_level: ReportRiskLevel
    combined_score: float = Field(..., ge=0, le=1)
    combined_confidence: float = Field(..., ge=0, le=1)
    headline: str
    summary: str
    primary_reasons: list[ReportReason] = Field(default_factory=list)
    quantitative_summary: QuantitativeSummary
    support_summary: SupportSummary
    evidence: list[ReportEvidence] = Field(default_factory=list)
    data_quality_notes: list[str] = Field(default_factory=list)
    recommended_action: str | None = None


class RiskDistribution(BaseModel):
    """Counts per risk level (§5.26)."""

    model_config = ConfigDict(extra="forbid")

    critical: int = Field(..., ge=0)
    high: int = Field(..., ge=0)
    medium: int = Field(..., ge=0)
    low: int = Field(..., ge=0)
    insufficient_data: int = Field(..., ge=0)


class DataQualitySection(BaseModel):
    """Data quality notes (§5.26)."""

    model_config = ConfigDict(extra="forbid")

    notes: list[str] = Field(default_factory=list)


class ReportContent(BaseModel):
    """The report body (§5.26)."""

    model_config = ConfigDict(extra="forbid")

    title: str
    reference_date: date
    executive_summary: str
    risk_distribution: RiskDistribution
    priority_accounts: list[CustomerReport] = Field(default_factory=list)
    insufficient_data_accounts: list[CustomerReport] = Field(default_factory=list)
    data_quality: DataQualitySection
    methodology: str


class ReportMetadata(BaseModel):
    """Reproducibility metadata (§5.25/§5.26)."""

    model_config = ConfigDict(extra="forbid")

    report_version: str
    node2_model_version: str
    node3_signal_version: str
    node4_ranking_version: str
    node4_threshold_version: str
    node4_critical_rules_version: str
    prompt_version: str | None = None
    llm_model_version: str | None = None
    reference_date: date
    generated_at: datetime


class Node5ProcessingReport(BaseModel):
    """Node 5 processing report (§5.26)."""

    model_config = ConfigDict(extra="forbid")

    n_accounts: int = Field(..., ge=0)
    n_accounts_reported: int = Field(..., ge=0)
    n_insufficient_data: int = Field(..., ge=0)
    llm_calls: int = Field(..., ge=0)
    llm_failures: int = Field(..., ge=0)
    validation_errors: int = Field(..., ge=0)
    warnings: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class Node5Output(BaseModel):
    """Full Node 5 output (§5.26)."""

    model_config = ConfigDict(extra="forbid")

    report: ReportContent
    metadata: ReportMetadata
    processing_report: Node5ProcessingReport

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

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
from schemas.node2 import DriverDetail
from schemas.node4 import ConfidenceFactorsOut, ForwardStatus, ReasonSource


class ReportReason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_type: ReasonType
    source: ReasonSource
    severity: ReportSeverity
    statement: str
    evidence_ref: str | dict[str, Any]


class ReportFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    flag_type: FlagType
    severity: Severity
    signal_strength: SignalStrength


class Node2ReportReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version: str
    feature_ref: str | None = None


class Node3ReportReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    thread_id: str
    message_id: str
    timestamp: datetime
    evidence_text: str


class ReportEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: ReasonSource
    description: str
    node2_reference: Node2ReportReference | None = None
    node3_reference: Node3ReportReference | None = None


class QuantitativeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    risk_score: float | None = Field(default=None, ge=0, le=1)
    survival_prob_90d: float | None = Field(default=None, ge=0, le=1)
    top_drivers: list[str] = Field(default_factory=list)
    customer_state: CustomerState
    churn_prob_90d_forward: float | None = Field(default=None, ge=0, le=1)
    lift_vs_base: float | None = Field(default=None, ge=0)
    forward_status: ForwardStatus | None = None
    driver_details: list[DriverDetail] = Field(default_factory=list)


class SupportSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    support_data_status: SupportDataStatus
    signal_strength: OverallSignalStrength
    churn_language_detected: bool = False
    escalation_signal: bool = False
    top_flags: list[ReportFlag] = Field(default_factory=list)


class CustomerReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    display_name: str
    rank: int | None = Field(default=None, ge=1)
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
    explanation_source: Literal["llm", "template"] = "template"
    confidence_factors: ConfidenceFactorsOut | None = None


class RiskDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    critical: int = Field(..., ge=0)
    high: int = Field(..., ge=0)
    medium: int = Field(..., ge=0)
    low: int = Field(..., ge=0)
    insufficient_data: int = Field(..., ge=0)


class DataQualitySection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: list[str] = Field(default_factory=list)


class ChurnedSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n_churned: int = Field(default=0, ge=0)
    customer_ids: list[str] = Field(default_factory=list)


class ReportContent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    reference_date: date
    executive_summary: str
    risk_distribution: RiskDistribution
    priority_accounts: list[CustomerReport] = Field(default_factory=list)
    insufficient_data_accounts: list[CustomerReport] = Field(default_factory=list)
    churned: ChurnedSection = Field(default_factory=ChurnedSection)
    data_quality: DataQualitySection
    methodology: str


class ReportMetadata(BaseModel):
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
    action_rules_version: str | None = None


class Node5ProcessingReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n_accounts: int = Field(..., ge=0)
    n_accounts_reported: int = Field(..., ge=0)
    n_insufficient_data: int = Field(..., ge=0)
    n_churned: int = Field(default=0, ge=0)
    llm_calls: int = Field(..., ge=0)
    llm_failures: int = Field(..., ge=0)
    explanation_source_summary: dict[str, int] = Field(default_factory=dict)
    validation_errors: int = Field(..., ge=0)
    warnings: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class Node5Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report: ReportContent
    metadata: ReportMetadata
    processing_report: Node5ProcessingReport

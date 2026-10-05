from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.enums import (
    CustomerState,
    ModelStatus,
    OverallSignalStrength,
    ReasonType,
    RiskLevel,
    SupportDataStatus,
)
from schemas.node2 import DriverDetail

ReasonSource = Literal["node2", "node3"]
ForwardStatus = Literal["available", "beyond_follow_up", "unavailable"]


class StructuredReason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_type: ReasonType
    source: ReasonSource
    severity: str
    evidence_ref: str | dict[str, Any]


class QuantitativeInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_status: ModelStatus
    risk_score: float | None = Field(default=None, ge=0, le=1)
    survival_prob_90d: float | None = Field(default=None, ge=0, le=1)
    normalized_risk: float | None = Field(default=None, ge=0, le=1)
    top_drivers: list[str] = Field(default_factory=list)
    customer_state: CustomerState
    churn_prob_90d_forward: float | None = Field(default=None, ge=0, le=1)
    lift_vs_base: float | None = Field(default=None, ge=0)
    forward_status: ForwardStatus | None = None
    driver_details: list[DriverDetail] = Field(default_factory=list)
    relative_log_hazard: float | None = None


class QualitativeInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    support_data_status: SupportDataStatus
    signal_strength: OverallSignalStrength
    overall_signal_confidence: float = Field(..., ge=0, le=1)
    churn_language_detected: bool = False
    top_flags: list[dict[str, Any]] = Field(default_factory=list)
    escalation_signal: bool = False


class Node2EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_version: str
    customer_state: CustomerState
    feature_refs: list[Any] = Field(default_factory=list)


class Node3EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signal_version: str
    thread_ids: list[str] = Field(default_factory=list)
    message_ids: list[str] = Field(default_factory=list)


class EvidenceRefs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node2: Node2EvidenceRef
    node3: Node3EvidenceRef


class RankedAccountMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ranked_at: datetime
    ranking_version: str
    threshold_version: str
    critical_rules_version: str


class ConfidenceFactorsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: float = Field(..., ge=0, le=1)
    precision: float = Field(..., ge=0, le=1)
    history: float = Field(..., ge=0, le=1)
    quantitative: float = Field(..., ge=0, le=1)
    support: float | None = Field(default=None, ge=0, le=1)


class RankedAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    rank: int | None = Field(default=None, ge=1)
    combined_risk_level: RiskLevel
    combined_score: float = Field(..., ge=0, le=1)
    combined_confidence: float = Field(..., ge=0, le=1)
    quantitative: QuantitativeInfo
    qualitative: QualitativeInfo
    primary_reasons: list[StructuredReason] = Field(default_factory=list)
    evidence_refs: EvidenceRefs
    explanation: str | None = None
    meta: RankedAccountMeta
    confidence_factors: ConfidenceFactorsOut | None = None


class ChurnedAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str
    tenure_days: float | None = Field(default=None, ge=0)
    evidence_refs: Node2EvidenceRef


class SummaryStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n_customers: int = Field(..., ge=0)
    n_critical: int = Field(..., ge=0)
    n_high: int = Field(..., ge=0)
    n_medium: int = Field(..., ge=0)
    n_low: int = Field(..., ge=0)
    n_insufficient_data: int = Field(..., ge=0)
    n_churned: int = Field(default=0, ge=0)


class Node4ProcessingReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warnings: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class Node4Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ranked_accounts: list[RankedAccount] = Field(default_factory=list)
    insufficient_data_accounts: list[RankedAccount] = Field(default_factory=list)
    churned_accounts: list[ChurnedAccount] = Field(default_factory=list)
    summary_stats: SummaryStats
    reference_date: date
    processing_report: Node4ProcessingReport

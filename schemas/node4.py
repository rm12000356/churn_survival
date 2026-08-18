"""Node 4 contracts (architecture §4.21/§4.22/§4.24, ROADMAP Task 1.1).

Ranked accounts, deterministic structured reasons, and the full Node 4 output.
"""

from __future__ import annotations

from datetime import datetime
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

ReasonSource = Literal["node2", "node3"]


class StructuredReason(BaseModel):
    """Deterministic reason explaining a classification (§4.22)."""

    model_config = ConfigDict(extra="forbid")

    reason_type: ReasonType
    source: ReasonSource
    severity: str
    evidence_ref: str | dict[str, Any]


class QuantitativeInfo(BaseModel):
    """Node 2 evidence as carried inside a ranked account (§4.21)."""

    model_config = ConfigDict(extra="forbid")

    model_status: ModelStatus
    risk_score: float | None = Field(default=None, ge=0, le=1)
    survival_prob_90d: float | None = Field(default=None, ge=0, le=1)
    normalized_risk: float | None = Field(default=None, ge=0, le=1)
    top_drivers: list[str] = Field(default_factory=list)
    customer_state: CustomerState


class QualitativeInfo(BaseModel):
    """Node 3 evidence as carried inside a ranked account (§4.21)."""

    model_config = ConfigDict(extra="forbid")

    support_data_status: SupportDataStatus
    signal_strength: OverallSignalStrength
    overall_signal_confidence: float = Field(..., ge=0, le=1)
    churn_language_detected: bool = False
    top_flags: list[dict[str, Any]] = Field(default_factory=list)
    escalation_signal: bool = False


class Node2EvidenceRef(BaseModel):
    """Node 2 evidence reference (§4.23)."""

    model_config = ConfigDict(extra="forbid")

    model_version: str
    customer_state: CustomerState
    feature_refs: list[Any] = Field(default_factory=list)


class Node3EvidenceRef(BaseModel):
    """Node 3 evidence reference (§4.23)."""

    model_config = ConfigDict(extra="forbid")

    signal_version: str
    thread_ids: list[str] = Field(default_factory=list)
    message_ids: list[str] = Field(default_factory=list)


class EvidenceRefs(BaseModel):
    """Evidence references for a ranked account (§4.21)."""

    model_config = ConfigDict(extra="forbid")

    node2: Node2EvidenceRef
    node3: Node3EvidenceRef


class RankedAccountMeta(BaseModel):
    """Ranked-account versioning metadata (§4.21)."""

    model_config = ConfigDict(extra="forbid")

    ranked_at: datetime
    ranking_version: str
    threshold_version: str
    critical_rules_version: str


class RankedAccount(BaseModel):
    """One ranked account (§4.21)."""

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


class SummaryStats(BaseModel):
    """Counts that must equal the actual output lists (§4.24)."""

    model_config = ConfigDict(extra="forbid")

    n_customers: int = Field(..., ge=0)
    n_critical: int = Field(..., ge=0)
    n_high: int = Field(..., ge=0)
    n_medium: int = Field(..., ge=0)
    n_low: int = Field(..., ge=0)
    n_insufficient_data: int = Field(..., ge=0)


class Node4ProcessingReport(BaseModel):
    """Node 4 processing report (§4.24)."""

    model_config = ConfigDict(extra="forbid")

    warnings: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class Node4Output(BaseModel):
    """Full Node 4 output (§4.24)."""

    model_config = ConfigDict(extra="forbid")

    ranked_accounts: list[RankedAccount] = Field(default_factory=list)
    insufficient_data_accounts: list[RankedAccount] = Field(default_factory=list)
    summary_stats: SummaryStats
    processing_report: Node4ProcessingReport

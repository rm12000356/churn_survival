"""Node 4 contracts (architecture §4.21/§4.22/§4.24, ROADMAP Task 1.1).

Ranked accounts, deterministic structured reasons, and the full Node 4 output.
"""

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
#: Why a forward value is (not) present (phase 10, D-R5). ``beyond_follow_up``:
#: the customer's next-90-day window ends past the model's longest observed
#: tenure, so no forward value exists — treated as missing, never as low risk.
ForwardStatus = Literal["available", "beyond_follow_up", "unavailable"]


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
    # Phase 10 (risk_norm_v2): forward 90-day churn probability ``1 − S(T+90)/S(T)``
    # and its ratio to the run's base rate. None under risk_norm_v1.
    churn_prob_90d_forward: float | None = Field(default=None, ge=0, le=1)
    lift_vs_base: float | None = Field(default=None, ge=0)
    forward_status: ForwardStatus | None = None
    # Amendment 2026-10-01 (model contributions, §4.4b): this account's own
    # selected drivers (explanation metadata only — never a decision input) and
    # its log-hazard relative to the model reference profile.
    driver_details: list[DriverDetail] = Field(default_factory=list)
    relative_log_hazard: float | None = None


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


class ConfidenceFactorsOut(BaseModel):
    """conf_v2 breakdown of a customer's confidence (phase 10, D-R4).

    ``model`` × ``precision`` × ``history`` is the quantitative confidence;
    ``support`` is Node 3's confidence when support was supplied (else None).
    """

    model_config = ConfigDict(extra="forbid")

    model: float = Field(..., ge=0, le=1)
    precision: float = Field(..., ge=0, le=1)
    history: float = Field(..., ge=0, le=1)
    quantitative: float = Field(..., ge=0, le=1)
    support: float | None = Field(default=None, ge=0, le=1)


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
    #: conf_v2 only; None under conf_v1 (run-level confidence proxy).
    confidence_factors: ConfidenceFactorsOut | None = None


class ChurnedAccount(BaseModel):
    """A customer who already churned (event observed) — not ranked (phase 10, D-R3)."""

    model_config = ConfigDict(extra="forbid")

    customer_id: str
    tenure_days: float | None = Field(default=None, ge=0)
    evidence_refs: Node2EvidenceRef


class SummaryStats(BaseModel):
    """Counts that must equal the actual output lists (§4.24)."""

    model_config = ConfigDict(extra="forbid")

    n_customers: int = Field(..., ge=0)
    n_critical: int = Field(..., ge=0)
    n_high: int = Field(..., ge=0)
    n_medium: int = Field(..., ge=0)
    n_low: int = Field(..., ge=0)
    n_insufficient_data: int = Field(..., ge=0)
    #: Customers in ``churned_accounts``; not part of ``n_customers`` (D-R3).
    n_churned: int = Field(default=0, ge=0)


class Node4ProcessingReport(BaseModel):
    """Node 4 processing report (§4.24)."""

    model_config = ConfigDict(extra="forbid")

    warnings: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


class Node4Output(BaseModel):
    """Full Node 4 output (§4.24).

    ``reference_date`` records the declared cut-off for the run (§4.28); it is a
    single run-level field, separate from the per-account ``meta.ranked_at`` (D-4).
    """

    model_config = ConfigDict(extra="forbid")

    ranked_accounts: list[RankedAccount] = Field(default_factory=list)
    insufficient_data_accounts: list[RankedAccount] = Field(default_factory=list)
    #: Already-churned customers, separate from ranking (phase 10, D-R3).
    churned_accounts: list[ChurnedAccount] = Field(default_factory=list)
    summary_stats: SummaryStats
    reference_date: date
    processing_report: Node4ProcessingReport

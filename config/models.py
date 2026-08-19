"""Versioned decision-config models (ROADMAP Task 1.3, architecture §4.2/§5.3/§5.18/§1.5).

All decision logic is driven by these objects. They are immutable at runtime
(`frozen`); any change requires a new versioned file, never an in-place edit.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.enums import FlagType, OverallSignalStrength, SignalStrength
from schemas.mapping import MappingReport


class RiskThresholds(BaseModel):
    """Combined-score thresholds (§4.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    medium: float = Field(..., ge=0, le=1)
    high: float = Field(..., ge=0, le=1)


class QuantitativeThresholds(BaseModel):
    """Normalized quantitative-risk thresholds (§4.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    low: float = Field(..., ge=0, le=1)
    medium: float = Field(..., ge=0, le=1)
    high: float = Field(..., ge=0, le=1)


class ConfidenceWeights(BaseModel):
    """Confidence combination weights (§4.2). Must sum to 1.0."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    quantitative: float = Field(..., ge=0, le=1)
    qualitative: float = Field(..., ge=0, le=1)


class Node2Config(BaseModel):
    """Node 2 decision configuration (architecture §2.4/§2.6/§2.7, ROADMAP Tasks 3.1–3.8).

    Drives model eligibility, multicollinearity handling, CoxPH fitting,
    horizon availability, Kaplan-Meier segment gating, cold-start state, and
    assumption-check severity. Frozen; changes require a new versioned file,
    never an in-place edit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    eligibility_version: str
    modeling_version: str
    horizon_version: str

    # Eligibility hard gates (§2.4)
    min_customers: int = Field(default=200, ge=1)
    min_events: int = Field(default=30, ge=1)
    min_events_per_predictor: float = Field(default=10.0, gt=0)
    missingness_threshold: float = Field(default=0.30, ge=0, le=1)
    min_variation_fraction: float = Field(default=0.05, ge=0, le=1)

    # Multicollinearity (§2.5)
    correlation_threshold: float = Field(default=0.95, ge=0, le=1)
    vif_threshold: float = Field(default=5.0, ge=1)
    vif_min_predictors: int = Field(default=3, ge=1)

    # CoxPH (§2.6)
    penalizer: float = Field(default=0.1, gt=0)
    tie_method: Literal["efron", "breslow"] = "efron"

    # Horizons (§2.7)
    horizons: list[int]
    horizon_min_observed_customers: int = Field(default=50, ge=1)
    horizon_min_events_around: int = Field(default=5, ge=1)
    horizon_max_ci_width: float = Field(default=0.30, gt=0, le=2)

    # Kaplan-Meier segments (§2.8)
    km_segment_min_customers: int = Field(default=50, ge=1)
    km_segment_min_events: int = Field(default=10, ge=1)

    # Cold-start (§2.9)
    cold_start_max_tenure_days: float = Field(default=30.0, ge=0)
    cold_start_min_behavioral_features: int = Field(default=1, ge=0)

    # Validation (§2.6)
    bootstrap_iterations: int = Field(default=200, ge=10)
    ph_p_value_warning: float = Field(default=0.05, gt=0, le=1)
    ph_p_value_serious: float = Field(default=0.01, gt=0, le=1)


class Node4Config(BaseModel):
    """Node 4 decision configuration (architecture §4.2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    ranking_version: str
    threshold_version: str
    critical_rules_version: str

    quantitative_weight: float = Field(..., ge=0, le=1)
    qualitative_weight: float = Field(..., ge=0, le=1)
    agreement_bonus: float = Field(..., ge=0, le=1)

    risk_thresholds: RiskThresholds
    quantitative_thresholds: QuantitativeThresholds
    confidence_weights: ConfidenceWeights

    hierarchy_weights: dict[FlagType, float]
    strength_scores: dict[SignalStrength, float]
    strength_order: dict[OverallSignalStrength, int]

    reference_date: date

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> Self:
        if not math.isclose(self.quantitative_weight + self.qualitative_weight, 1.0, abs_tol=1e-6):
            raise ValueError(
                "quantitative_weight + qualitative_weight must sum to 1.0 "
                f"(got {self.quantitative_weight + self.qualitative_weight})"
            )
        if not math.isclose(
            self.confidence_weights.quantitative + self.confidence_weights.qualitative,
            1.0,
            abs_tol=1e-6,
        ):
            raise ValueError("confidence_weights must sum to 1.0")
        return self


class Node5Config(BaseModel):
    """Node 5 input configuration (architecture §5.3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_version: str
    prompt_version: str
    model_version: str | None = None
    reference_date: date
    include_insufficient_data: bool = False
    include_evidence: bool = True
    include_recommendations: bool = True
    max_accounts_in_summary: int = Field(..., ge=1)
    max_evidence_per_account: int = Field(..., ge=0)
    language: str = "en"


class ActionRulesConfig(BaseModel):
    """Versioned recommended-action mappings (architecture §5.18)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_rules_version: str
    rules: dict[str, str]


class TenureSanityParams(BaseModel):
    """Tenure-distribution sanity gate parameters (architecture §1.7)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_zero_fraction: float = Field(default=0.80, ge=0, le=1)
    max_extreme_outlier_ratio: float = Field(default=0.10, ge=0, le=1)
    outlier_std_factor: float = Field(default=10.0, gt=0)


CoreKeyType = Literal["string", "float", "int"]


class Node1Config(BaseModel):
    """Node 1 decision configuration (architecture §1.7, ROADMAP Tasks 2.1–2.9).

    Drives validation hard gates, the router's high-confidence threshold, and
    the feature-gate promotion defaults. Frozen; changes require a new versioned
    file, never an in-place edit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    validation_version: str
    missingness_threshold: float = Field(default=0.30, ge=0, le=1)
    router_high_confidence_threshold: float = Field(default=0.80, ge=0, le=1)
    promotion_min_events: int = Field(default=30, ge=1)
    approved_core_keys: list[str]
    core_key_types: dict[str, CoreKeyType] = Field(default_factory=dict)
    tenure_sanity: TenureSanityParams


class MappingConfig(BaseModel):
    """A human-confirmed mapping, persisted as a deterministic config (architecture §1.5/§1.6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mapping_version: str
    report: MappingReport
    confirmed_at: datetime | None = None
    confirmed_by: str | None = None

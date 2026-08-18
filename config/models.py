"""Versioned decision-config models (ROADMAP Task 1.3, architecture §4.2/§5.3/§5.18/§1.5).

All decision logic is driven by these objects. They are immutable at runtime
(`frozen`); any change requires a new versioned file, never an in-place edit.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Self

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
        if not math.isclose(
            self.quantitative_weight + self.qualitative_weight, 1.0, abs_tol=1e-6
        ):
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


class MappingConfig(BaseModel):
    """A human-confirmed mapping, persisted as a deterministic config (architecture §1.5/§1.6)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mapping_version: str
    report: MappingReport
    confirmed_at: datetime | None = None
    confirmed_by: str | None = None

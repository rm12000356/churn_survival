"""Node 2 contracts: model artifact (§2.11) and final output (§2.12).

ROADMAP Task 1.1. `risk_scores` is null whenever individual Cox-style scores
cannot be produced; survival horizon statuses are AVAILABLE/INSUFFICIENT_DATA.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from schemas.enums import CustomerState, HorizonStatus, ModelStatus, ModelType


class HorizonResult(BaseModel):
    """One survival-probability horizon (§2.12)."""

    model_config = ConfigDict(extra="forbid")

    status: HorizonStatus
    values: list[float] | None = None
    ci: list[list[float]] | None = None


class FeatureAssociation(BaseModel):
    """One model feature → hazard-ratio interpretation (§2.12)."""

    model_config = ConfigDict(extra="forbid")

    feature: str
    coefficient: float
    hazard_ratio: float
    ci_lower: float
    ci_upper: float
    p_value: float
    interpretation: str


class ModelArtifact(BaseModel):
    """Fully versioned model artifact metadata (§2.11). Enables exact rescoring."""

    model_config = ConfigDict(extra="forbid")

    model_version: str
    training_timestamp: datetime
    training_dataset_version: str
    reference_date: date
    selected_features: list[str]
    coefficients: dict[str, float]
    baseline: dict[str, Any]
    penalizer: float
    n_customers: int = Field(..., ge=0)
    n_events: int = Field(..., ge=0)
    encoding_scheme: dict[str, Any]
    validation_metrics: dict[str, Any]
    assumption_check_results: dict[str, Any]
    horizon_config: list[int]


class Node2Output(BaseModel):
    """Final Node 2 output — identical for all paths (§2.12).

    Alignment contract: ``customer_ids`` and ``customer_states`` are parallel and
    cover the full customer universe in deterministic order (``customer_id``
    ascending). ``risk_scores`` and each horizon's ``values``/``ci`` cover only
    the *scored* customers: the i-th scored customer is the i-th entry of
    ``customer_ids`` whose state is ``"scored"`` (states ``not_enough_data`` /
    ``excluded`` carry no value).
    """

    model_config = ConfigDict(extra="forbid")

    model_type: ModelType
    model_status: ModelStatus
    model_version: str
    risk_scores: list[float] | None = None
    survival_probabilities: dict[str, HorizonResult]
    feature_associations: list[FeatureAssociation] | None = None
    validation_metrics: dict[str, Any] = Field(default_factory=dict)
    assumption_checks: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    customer_ids: list[str] = Field(default_factory=list)
    customer_states: list[CustomerState] = Field(default_factory=list)

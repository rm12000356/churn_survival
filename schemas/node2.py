from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.enums import CustomerState, HorizonStatus, ModelStatus, ModelType


class HorizonResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: HorizonStatus
    values: list[float] | None = None
    ci: list[list[float | None]] | None = None
    ci_approximate: bool = False


class ForwardHorizonResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: list[float | None]
    ci: list[list[float | None]]
    ci_approximate: bool = False


class FeatureAssociation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature: str
    coefficient: float
    hazard_ratio: float
    ci_lower: float
    ci_upper: float
    p_value: float
    interpretation: str


class FeatureContribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature: str
    column: str
    kind: Literal["numeric", "categorical"]
    value: float | str
    reference: float | str | None
    coefficient: float
    hazard_ratio: float
    contribution: float
    reliable: bool


class DriverDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feature: str
    kind: Literal["numeric", "categorical"]
    value: float | str
    reference: float | str | None
    contribution: float
    hazard_ratio: float
    reliable: bool


class ModelArtifact(BaseModel):
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
    customer_tenure_days: list[float] | None = None
    customer_event_observed: list[int] | None = None
    forward_survival: dict[str, ForwardHorizonResult] | None = None
    max_follow_up_days: float | None = None
    baseline_log_hazard: float | None = None
    customer_relative_log_hazard: list[float | None] | None = None
    customer_contributions: list[list[FeatureContribution]] | None = None

"""Node 2 contracts: model artifact (§2.11) and final output (§2.12).

ROADMAP Task 1.1. `risk_scores` is null whenever individual Cox-style scores
cannot be produced; survival horizon statuses are AVAILABLE/INSUFFICIENT_DATA.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.enums import CustomerState, HorizonStatus, ModelStatus, ModelType


class HorizonResult(BaseModel):
    """One survival-probability horizon (§2.12)."""

    model_config = ConfigDict(extra="forbid")

    status: HorizonStatus
    values: list[float] | None = None
    #: Per-customer ``[lower, upper]``; a bound is ``None`` when it cannot be
    #: computed (e.g. no events before the horizon) — never NaN (REVIEW N-M14).
    ci: list[list[float | None]] | None = None
    #: True for the CoxPH delta-method band, which ignores ``exp(x·β)`` risk-set
    #: weights and per-stratum baselines: indicative, not an exact 95% interval.
    ci_approximate: bool = False


class ForwardHorizonResult(BaseModel):
    """Forward (conditional) survival over the next ``t`` days (§2.12 amendment 2026-10-01).

    ``values[i]`` is ``S(T_i + t) / S(T_i)``: the probability that the i-th
    scored customer, alive at current tenure ``T_i``, survives the next ``t``
    days. ``None`` for customers who already churned and for customers whose
    ``T_i + t`` lies beyond the fit data's longest observed tenure (no tail
    extrapolation). Aligned to the scored subset, like ``HorizonResult``.
    """

    model_config = ConfigDict(extra="forbid")

    values: list[float | None]
    ci: list[list[float | None]]
    #: True for CoxPH: the band reuses the global Kaplan-Meier Greenwood
    #: variance over the same window — indicative, not an exact 95% interval.
    ci_approximate: bool = False


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


class FeatureContribution(BaseModel):
    """One fitted predictor's contribution to an account's relative log-hazard (§2.12b).

    ``contribution = β · (x − reference)``, relative to the *model reference
    profile* (numerics at their training mean, categoricals at their reference
    category). Emitted only for fitted predictor columns: numerics always,
    categorical dummies only when active (1.0). The reference category has no
    fitted column and therefore no record; it contributes 0 implicitly.
    """

    model_config = ConfigDict(extra="forbid")

    #: Human feature name ("usage_frequency", "plan_tier").
    feature: str
    #: Fitted encoded column ("usage_frequency", "plan_tier_starter").
    column: str
    kind: Literal["numeric", "categorical"]
    #: This account's own value (numeric value or category label).
    value: float | str
    #: Numeric: training mean; categorical: reference-category label.
    reference: float | str | None
    coefficient: float
    hazard_ratio: float
    #: Signed contribution to relative log-hazard; full precision.
    contribution: float
    #: True when the coefficient's 95% CI excludes 1.0.
    reliable: bool


class DriverDetail(BaseModel):
    """Report projection of a selected per-account driver (Node 4 policy, §4.4b)."""

    model_config = ConfigDict(extra="forbid")

    feature: str
    kind: Literal["numeric", "categorical"]
    value: float | str
    reference: float | str | None
    #: Signed contribution to relative log-hazard; full precision.
    contribution: float
    hazard_ratio: float
    #: Provenance of the selection policy (the contribution's reliability).
    reliable: bool


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
    # Amendment 2026-10-01 (forward-looking risk). Optional so older outputs
    # still validate. Tenure/event are parallel to ``customer_ids`` (full
    # universe); ``forward_survival`` is aligned to the scored subset.
    customer_tenure_days: list[float] | None = None
    customer_event_observed: list[int] | None = None
    forward_survival: dict[str, ForwardHorizonResult] | None = None
    #: Longest observed tenure in the fit data; forward windows past it are None.
    max_follow_up_days: float | None = None
    # Amendment 2026-10-01 (model contributions, §2.12b). Optional so older
    # outputs still validate; None for Kaplan-Meier / no-model outputs. Both
    # lists are aligned to the scored subset, like ``risk_scores``.
    #: ``Σ β_j · ref_j`` over numeric predictors (the reference profile's LP).
    baseline_log_hazard: float | None = None
    #: ``LP_i − baseline_log_hazard`` = sum of the customer's contributions.
    customer_relative_log_hazard: list[float | None] | None = None
    customer_contributions: list[list[FeatureContribution]] | None = None

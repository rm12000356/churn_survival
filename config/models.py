from __future__ import annotations

import math
from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.enums import EvidenceMode, FlagType, OverallSignalStrength, SignalStrength
from schemas.mapping import FEATURE_KEY_PATTERN, ApprovedFeature, FeatureKind, MappingReport


class RiskThresholds(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    medium: float = Field(..., ge=0, le=1)
    high: float = Field(..., ge=0, le=1)


class QuantitativeThresholds(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    low: float = Field(..., ge=0, le=1)  # lower band boundary; reserved for future use
    medium: float = Field(..., ge=0, le=1)
    high: float = Field(..., ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not (self.low <= self.medium <= self.high):
            raise ValueError(
                "quantitative_thresholds must satisfy low <= medium <= high, got "
                f"low={self.low}, medium={self.medium}, high={self.high}"
            )
        return self


class ConfidenceWeights(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    quantitative: float = Field(..., ge=0, le=1)
    qualitative: float = Field(..., ge=0, le=1)


class Node2Config(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    eligibility_version: str
    modeling_version: str
    horizon_version: str

    min_customers: int = Field(default=200, ge=1)
    min_events: int = Field(default=30, ge=1)
    min_events_per_predictor: float = Field(default=10.0, gt=0)
    missingness_threshold: float = Field(default=0.30, ge=0, le=1)
    min_variation_fraction: float = Field(default=0.05, ge=0, le=1)

    correlation_threshold: float = Field(default=0.95, ge=0, le=1)
    vif_threshold: float = Field(default=5.0, ge=1)
    vif_min_predictors: int = Field(default=3, ge=1)

    penalizer: float = Field(default=0.1, gt=0)
    tie_method: Literal["efron"] = "efron"

    horizons: list[int]
    horizon_min_observed_customers: int = Field(default=50, ge=1)
    horizon_min_events_around: int = Field(default=5, ge=1)
    horizon_max_ci_width: float = Field(default=0.30, gt=0, le=2)

    km_segment_min_customers: int = Field(default=50, ge=1)
    km_segment_min_events: int = Field(default=10, ge=1)

    cold_start_max_tenure_days: float = Field(default=30.0, ge=0)
    cold_start_min_behavioral_features: int = Field(default=1, ge=0)

    bootstrap_iterations: int = Field(default=200, ge=10)
    ph_p_value_warning: float = Field(default=0.05, gt=0, le=1)
    ph_p_value_serious: float = Field(default=0.01, gt=0, le=1)


class Node3Config(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    aggregation_version: str
    vocabulary_version: str
    preprocessing_version: str
    prompt_version: str

    lookback_days: int = Field(default=365, ge=0)
    max_threads_per_customer: int = Field(default=50, ge=1)
    max_messages_per_thread: int = Field(default=100, ge=1)
    max_tokens_per_customer: int = Field(default=50000, ge=1)

    supported_languages: list[str] = Field(default_factory=lambda: ["en"])

    lambda_default: float = Field(default=0.015, gt=0)
    lambda_persistent: float = Field(default=0.004, gt=0)
    persistent_flag_types: list[str] = Field(
        default_factory=lambda: ["cancellation_intent", "renewal_or_contract_concern"]
    )

    dedup_time_window_hours: int = Field(default=48, ge=0)
    dedup_tfidf_threshold: float = Field(default=0.82, ge=0, le=1)
    dedup_subject_threshold: float = Field(default=0.75, ge=0, le=1)

    llm_temperature: float = Field(default=0.2, ge=0, le=0.2)
    llm_max_retries: int = Field(default=1, ge=0)
    llm_max_concurrency: int = Field(default=8, ge=1, le=64)
    llm_max_consecutive_failures: int = Field(default=5, ge=1)

    limited_data_min_customer_messages: int = Field(default=3, ge=1)

    reference_date: date


class SourceSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    mode: Literal["mock", "live"] | None = None
    mock_dir: str | None = None
    agent_identities: list[str] = Field(default_factory=list)
    include_public: bool = True
    include_dms: bool = False


class Node3SourcesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    sources_version: str
    identity_mapping_version: str
    sources: dict[str, SourceSpec] = Field(default_factory=dict)


class IdentityMappingConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mapping_version: str
    mappings: dict[str, dict[str, str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _identities_and_customers_are_non_empty(self) -> Self:
        for source, pairs in self.mappings.items():
            if not source.strip():
                raise ValueError("identity mapping source keys must be non-empty")
            for identity, customer_id in pairs.items():
                if not identity.strip() or not customer_id.strip():
                    raise ValueError(
                        f"identity mapping for source {source!r} must have non-empty "
                        "external identities and customer_ids"
                    )
        return self


class VocabularyGovernance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    review_interval_weeks: int = Field(default=4, ge=1)
    other_review_threshold_pct: float = Field(default=20.0, ge=0, le=100)


class VocabularyConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    vocabulary_version: str
    ranks: dict[FlagType, int | None]
    governance: VocabularyGovernance = Field(default_factory=VocabularyGovernance)


class ConfidenceFactors(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    precision_max_ci_width: float = Field(..., gt=0, le=1)
    precision_floor: float = Field(default=0.5, ge=0, le=1)
    history_maturity_days: float = Field(..., gt=0)
    history_floor: float = Field(..., ge=0, le=1)


class Node4Config(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ranking_version: str
    threshold_version: str
    critical_rules_version: str
    normalization_version: str

    top_drivers_max: int = Field(default=5, ge=0)

    quantitative_weight: float = Field(..., ge=0, le=1)
    qualitative_weight: float = Field(..., ge=0, le=1)
    agreement_bonus: float = Field(..., ge=0, le=1)

    risk_thresholds: RiskThresholds
    quantitative_thresholds: QuantitativeThresholds
    confidence_weights: ConfidenceWeights

    quantitative_only_without_support: bool = False

    hierarchy_weights: dict[FlagType, float]
    strength_scores: dict[SignalStrength, float]
    strength_order: dict[OverallSignalStrength, int]

    reference_date: date

    risk_scale: Literal["absolute", "lift"] = "absolute"
    lift_points: list[tuple[float, float]] | None = None
    base_rate_min_customers: int = Field(default=30, ge=1)
    separate_churned: bool = False
    confidence_version: str = "conf_v1"
    confidence_factors: ConfidenceFactors | None = None

    per_customer_drivers: bool = False
    drivers_require_reliable: bool = False

    @model_validator(mode="after")
    def _lift_points_valid(self) -> Self:
        if self.risk_scale == "lift" and not self.lift_points:
            raise ValueError("risk_scale 'lift' requires lift_points")
        if self.lift_points:
            points = self.lift_points
            if points[0] != (0.0, 0.0):
                raise ValueError("lift_points must start at [0, 0]")
            for (x0, y0), (x1, y1) in zip(points, points[1:], strict=False):
                if x1 <= x0 or y1 < y0:
                    raise ValueError("lift_points must be strictly increasing in lift")
            if any(not 0.0 <= y <= 1.0 for _, y in points):
                raise ValueError("lift_points risk values must lie in [0, 1]")
        return self

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
        positive = self.hierarchy_weights.get(FlagType.POSITIVE_FEEDBACK)
        if positive is not None and positive != 0.0:
            raise ValueError(
                "hierarchy_weights[positive_feedback] must be 0.00 "
                "(positive feedback is contextual only and must never reduce risk)"
            )
        return self


class Node5Config(BaseModel):
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
    evidence_mode: EvidenceMode = EvidenceMode.SHORT_QUOTE
    llm_temperature: float = Field(default=0.2, ge=0, le=0.2)
    llm_max_retries: int = Field(default=1, ge=0)
    llm_enabled: bool = False
    llm_max_accounts: int = Field(default=25, ge=0)
    llm_max_consecutive_failures: int = Field(default=3, ge=1)
    llm_max_concurrency: int = Field(default=4, ge=1, le=32)


class ActionRulesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action_rules_version: str
    rules: dict[str, str]


class TenureSanityParams(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    max_zero_fraction: float = Field(default=0.80, ge=0, le=1)
    max_extreme_outlier_ratio: float = Field(default=0.05, ge=0, le=1)
    outlier_mad_factor: float = Field(default=10.0, gt=0)


CoreKeyType = Literal["string", "float", "int"]

RESERVED_FEATURE_KEYS = frozenset(
    {
        "customer_id",
        "observation_start",
        "observation_end",
        "event_observed",
        "tenure",
        "duration",
        "event",
    }
)


class DeclaredFeature(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: FeatureKind
    label: str = Field(..., min_length=1)


class FeatureScreeningConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    screening_version: str
    auc_block: float = Field(default=0.95, gt=0.5, le=1)
    auc_warn: float = Field(default=0.85, gt=0.5, le=1)
    presence_gap_block: float = Field(default=0.90, gt=0, le=1)
    presence_gap_warn: float = Field(default=0.50, gt=0, le=1)
    pure_level_min_share: float = Field(default=0.05, ge=0, le=1)
    pure_level_min_rows: int = Field(default=20, ge=1)
    tenure_correlation_block: float = Field(default=0.98, gt=0, le=1)
    max_levels_warn: int = Field(default=20, ge=2)
    max_levels_block: int = Field(default=50, ge=2)
    name_pattern: str

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.auc_warn > self.auc_block:
            raise ValueError("auc_warn must be <= auc_block")
        if self.presence_gap_warn > self.presence_gap_block:
            raise ValueError("presence_gap_warn must be <= presence_gap_block")
        if self.max_levels_warn > self.max_levels_block:
            raise ValueError("max_levels_warn must be <= max_levels_block")
        return self


class Node1Config(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    validation_version: str
    missingness_threshold: float = Field(default=0.30, ge=0, le=1)
    router_high_confidence_threshold: float = Field(default=0.80, ge=0, le=1)
    promotion_min_events: int = Field(default=30, ge=1)
    approved_core_keys: list[str]
    core_key_types: dict[str, CoreKeyType] = Field(default_factory=dict)
    tenure_sanity: TenureSanityParams
    allow_missing_core_passthrough: bool = Field(
        default=False,
        description=(
            "When true (architecture §1.7 amendment; dataset7 v1.2), an approved core "
            "key that is missing in a fraction of records at or below "
            "`missingness_threshold` passes through as a null core instead of "
            "quarantining those records — Node 2's complete-case rule then excludes "
            "them from the model matrix. Columns above the threshold still fail the "
            "batch exactly as before. Default false (strict quarantine)."
        ),
    )
    declared_features: dict[str, DeclaredFeature] = Field(
        default_factory=dict, exclude_if=lambda value: not value
    )

    @property
    def model_predictors(self) -> list[str]:
        return [*self.approved_core_keys, *sorted(self.declared_features)]

    @model_validator(mode="after")
    def _declared_features_are_safe(self) -> Self:
        import re

        from schemas.canonical import CoreFeatures

        declared = set(self.declared_features)
        bad = sorted(k for k in declared if not re.fullmatch(FEATURE_KEY_PATTERN, k))
        if bad:
            raise ValueError(f"declared feature keys must be snake_case: {bad}")
        clash = sorted(declared & (set(CoreFeatures.model_fields) | RESERVED_FEATURE_KEYS))
        if clash:
            raise ValueError(f"declared feature keys clash with reserved/core names: {clash}")
        names = declared | set(self.approved_core_keys)
        prefixed = sorted(
            f"{a} / {b}"
            for a in names
            for b in declared
            if a != b and (b.startswith(f"{a}_") or a.startswith(f"{b}_"))
        )
        if prefixed:
            raise ValueError(
                "declared feature keys would collide with one-hot column names "
                f"(<key>_<category>): {prefixed}"
            )
        return self

    @model_validator(mode="after")
    def _core_keys_are_known_and_typed(self) -> Self:
        from schemas.canonical import CoreFeatures

        unknown = sorted(set(self.approved_core_keys) - set(CoreFeatures.model_fields))
        if unknown:
            raise ValueError(
                f"approved_core_keys not in the CoreFeatures union: {unknown} "
                "(add the key to schemas/canonical.py first — see README.md onboarding)"
            )
        untyped = sorted(set(self.approved_core_keys) - set(self.core_key_types))
        if untyped:
            raise ValueError(f"core_key_types must declare a type for: {untyped}")
        return self


class MappingConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mapping_version: str
    report: MappingReport
    confirmed_at: datetime | None = None
    confirmed_by: str | None = None
    node1_config_version: str | None = Field(
        default=None,
        description=(
            "Deployment Node 1 config (`config/node1/v<version>.json`) that this "
            "confirmed mapping belongs to. Lets a full-pipeline run auto-resolve the "
            "correct `approved_core_keys` (architecture §1.7) from the matched mapping "
            "instead of falling back to the default config. `None` for legacy mappings "
            "confirmed before this linkage existed."
        ),
    )
    approved_features: list[ApprovedFeature] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    supersedes: str | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def _features_match_report(self) -> Self:
        targets = {
            m.target_field[len("feature."):]: m
            for m in self.report.proposed_mappings
            if m.target_field.startswith("feature.")
        }
        approved = {f.key: f for f in self.approved_features}
        if len(approved) != len(self.approved_features):
            raise ValueError("approved_features keys must be unique")
        if set(targets) != set(approved):
            raise ValueError(
                "approved_features must list exactly the report's feature.<key> targets; "
                f"targets={sorted(targets)} approved={sorted(approved)}"
            )
        for key, feature in approved.items():
            if targets[key].source_column != feature.source_column:
                raise ValueError(f"approved feature {key!r} source column mismatch")
        return self

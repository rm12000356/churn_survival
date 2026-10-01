"""Versioned decision-config models (ROADMAP Task 1.3, architecture §4.2/§5.3/§5.18/§1.5).

All decision logic is driven by these objects. They are immutable at runtime
(`frozen`); any change requires a new versioned file, never an in-place edit.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from schemas.enums import EvidenceMode, FlagType, OverallSignalStrength, SignalStrength
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


class Node3Config(BaseModel):
    """Node 3 decision configuration (architecture §3.2/§3.13, ROADMAP Task 4.1).

    Drives preprocessing, LLM extraction, and aggregation behavior.
    Frozen; changes require a new versioned file.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Versioning (§3.13)
    aggregation_version: str
    vocabulary_version: str
    preprocessing_version: str
    prompt_version: str

    # Limits (§3.2). `max_tokens_per_customer` is a hard cumulative cap over the
    # customer-authored tokens actually sent for a customer (LLM prompt content):
    # threads are considered newest-first, whole threads are kept while they fit,
    # and the first thread that would overflow is dropped with all older threads.
    lookback_days: int = Field(default=365, ge=0)
    max_threads_per_customer: int = Field(default=50, ge=1)
    max_messages_per_thread: int = Field(default=100, ge=1)
    max_tokens_per_customer: int = Field(default=50000, ge=1)

    # Language (§3.3)
    supported_languages: list[str] = Field(default_factory=lambda: ["en"])

    # Aggregation (§3.8)
    lambda_default: float = Field(default=0.015, gt=0)
    lambda_persistent: float = Field(default=0.004, gt=0)
    persistent_flag_types: list[str] = Field(
        default_factory=lambda: ["cancellation_intent", "renewal_or_contract_concern"]
    )

    # Cross-channel dedup (§3.3)
    dedup_time_window_hours: int = Field(default=48, ge=0)
    dedup_tfidf_threshold: float = Field(default=0.82, ge=0, le=1)
    dedup_subject_threshold: float = Field(default=0.75, ge=0, le=1)

    # LLM (§3.9). Temperature is bounded to <= 0.2 per the architecture.
    llm_temperature: float = Field(default=0.2, ge=0, le=0.2)
    llm_max_retries: int = Field(default=1, ge=0)
    # Bounded thread-level extraction concurrency (REVIEW §5). Per-thread outputs
    # are contract-identical and collected in input order, so this changes only
    # wall-clock time, never the result.
    llm_max_concurrency: int = Field(default=8, ge=1, le=64)
    # Circuit breaker (REVIEW N-H5): after this many consecutive provider
    # failures (in thread input order), the remaining threads are quarantined
    # without a call. Evaluated in input order, so the result does not depend on
    # llm_max_concurrency.
    llm_max_consecutive_failures: int = Field(default=5, ge=1)

    # support_data_status thresholds (§3.8.6)
    limited_data_min_customer_messages: int = Field(default=3, ge=1)

    reference_date: date


class SourceSpec(BaseModel):
    """Per-source ingestion config for Node 3 (multi-source addendum §3).

    ``mode`` selects the mock (credential-free) or live source implementation;
    ``mock_dir`` overrides the default mock fixture directory. ``agent_identities``
    declares the source-native identities belonging to the company (not the
    customer) so external authors can never be attached to a customer by accident.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    # None -> fall back to the environment's NODE3_SOURCE_MODE (default "mock").
    mode: Literal["mock", "live"] | None = None
    mock_dir: str | None = None
    agent_identities: list[str] = Field(default_factory=list)
    # X public data and private DMs are separate access paths; DMs require an
    # explicit opt-in and authorized credentials (addendum §7).
    include_public: bool = True
    include_dms: bool = False


class Node3SourcesConfig(BaseModel):
    """Versioned Node 3 external-source selection (multi-source addendum §3/§10).

    Frozen; adding or switching a source is a new versioned file, never an
    in-place edit. Credentials live in the environment (``config/settings.py``),
    never in this committed config.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sources_version: str
    identity_mapping_version: str
    sources: dict[str, SourceSpec] = Field(default_factory=dict)


class IdentityMappingConfig(BaseModel):
    """Deterministic external-identity -> customer_id mapping (addendum §6).

    ``mappings`` is ``{source: {external_identity: customer_id}}``. Identity
    resolution is an exact-match lookup — never fuzzy, never LLM-driven. A source
    identity that is absent from the mapping is left unresolved and cannot be
    attached to any customer.
    """

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
    """Vocabulary review cadence + ``other``-bucket alert threshold (§3.4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    review_interval_weeks: int = Field(default=4, ge=1)
    other_review_threshold_pct: float = Field(default=20.0, ge=0, le=100)


class VocabularyConfig(BaseModel):
    """Versioned controlled flag vocabulary + governance (architecture §3.4).

    ``ranks`` maps each ``FlagType`` to its hierarchy rank (1 = highest
    priority); ``positive_feedback`` and ``other`` are non-priority and map to
    ``None``. Frozen; a taxonomy change requires a new ``vocabulary_version``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    vocabulary_version: str
    ranks: dict[FlagType, int | None]
    governance: VocabularyGovernance = Field(default_factory=VocabularyGovernance)


class ConfidenceFactors(BaseModel):
    """conf_v2 per-customer quantitative confidence parameters (phase 10, D-R4)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: A forward-survival CI at least this wide gives precision 0.
    precision_max_ci_width: float = Field(..., gt=0, le=1)
    #: A NULL CI (not computable) gives this precision instead of 0 or 1.
    precision_floor: float = Field(default=0.5, ge=0, le=1)
    #: Tenure at which a customer's history is fully mature.
    history_maturity_days: float = Field(..., gt=0)
    #: History factor for a brand-new customer (tenure 0).
    history_floor: float = Field(..., ge=0, le=1)


class Node4Config(BaseModel):
    """Node 4 decision configuration (architecture §4.2)."""

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

    # Amendment 2026-09-30 (architecture §4.14a): support inputs are optional.
    # When a run supplies none, synthesis is quantitative-only: the combined
    # score and confidence use the model alone (no zero-weighted support term)
    # and no per-account missing-support / no-data conflict reasons are emitted.
    # Off in v1 so v1 outputs stay bit-identical; on from v2.
    quantitative_only_without_support: bool = False

    hierarchy_weights: dict[FlagType, float]
    strength_scores: dict[SignalStrength, float]
    strength_order: dict[OverallSignalStrength, int]

    reference_date: date

    # Amendment 2026-10-01 (phase 10, D-R1…D-R5). Every default reproduces the
    # v1/v2 behaviour, so those configs stay bit-identical; v3 turns them on.
    #: "absolute" = §4.4 ``1 − S(90d)``; "lift" = forward 90-day churn probability
    #: relative to the run's base rate, mapped through ``lift_points``.
    risk_scale: Literal["absolute", "lift"] = "absolute"
    #: Piecewise-linear ``[lift, normalized_risk]`` knots (lift mode only).
    lift_points: list[tuple[float, float]] | None = None
    #: Minimum active scored customers with a forward value to trust the base rate.
    base_rate_min_customers: int = Field(default=30, ge=1)
    #: Customers who already churned go to ``churned_accounts`` instead of ranking.
    separate_churned: bool = False
    confidence_version: str = "conf_v1"
    #: Per-customer quantitative confidence factors (conf_v2); None = run-level proxy.
    confidence_factors: ConfidenceFactors | None = None

    # Amendment 2026-10-01 (model contributions, §4.4b). Defaults reproduce the
    # v1–v3 model-wide D-2 drivers so those configs stay bit-identical; v4 turns
    # both on. Drivers are explanation metadata only — never a decision input.
    #: Select ``top_drivers`` per account from Node 2 ``customer_contributions``
    #: (positive contributions only) instead of the model-wide HR > 1 list.
    per_customer_drivers: bool = False
    #: Keep only drivers whose coefficient CI excludes 1.0.
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
    """Node 5 input configuration (architecture §5.3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    report_version: str
    prompt_version: str
    model_version: str | None = None
    reference_date: date
    include_insufficient_data: bool = False
    include_evidence: bool = True
    include_recommendations: bool = True
    # D-ORDER: max number of `priority_accounts` emitted — a deterministic prefix
    # of Node 4 ranked order. Never re-sorts; never changes risk_distribution.
    max_accounts_in_summary: int = Field(..., ge=1)
    max_evidence_per_account: int = Field(..., ge=0)
    language: str = "en"
    # D-U6: evidence amount when `include_evidence` is True. `include_evidence=False`
    # always wins and emits no evidence.
    evidence_mode: EvidenceMode = EvidenceMode.SHORT_QUOTE
    # D-U9: LLM explainer decoding (mirrors Node 3); bounded per architecture §3.9 style.
    llm_temperature: float = Field(default=0.2, ge=0, le=0.2)
    llm_max_retries: int = Field(default=1, ge=0)
    # LLM polish is opt-in and bounded (REVIEW §5): off unless explicitly enabled,
    # applied to at most the first `llm_max_accounts` priority accounts (Node 4
    # order), and abandoned for the rest of the run after
    # `llm_max_consecutive_failures` consecutive rejected accounts.
    llm_enabled: bool = False
    llm_max_accounts: int = Field(default=25, ge=0)
    llm_max_consecutive_failures: int = Field(default=3, ge=1)
    # Accounts explained concurrently per batch (Node 4 order preserved; the
    # breaker and cap are applied between batches, so 1 == fully sequential).
    llm_max_concurrency: int = Field(default=4, ge=1, le=32)


class ActionRulesConfig(BaseModel):
    """Versioned recommended-action mappings (architecture §5.18)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_rules_version: str
    rules: dict[str, str]


class TenureSanityParams(BaseModel):
    """Tenure-distribution sanity gate parameters (architecture §1.7).

    ``outlier_mad_factor`` scales the robust dispersion measure (median absolute
    deviation, MAD) — not the standard deviation, which a single extreme value
    inflates and hides. Tenures beyond ``median ± factor * MAD`` count as
    extreme outliers; a fraction above ``max_extreme_outlier_ratio`` fails the
    batch.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_zero_fraction: float = Field(default=0.80, ge=0, le=1)
    max_extreme_outlier_ratio: float = Field(default=0.05, ge=0, le=1)
    outlier_mad_factor: float = Field(default=10.0, gt=0)


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

    @model_validator(mode="after")
    def _core_keys_are_known_and_typed(self) -> Self:
        """Fail at load time, not mid-batch.

        An approved key outside the ``CoreFeatures`` union passes Gate 8 but then
        crashes ``build_report`` for the whole batch; an approved key without a
        declared type silently defaulted to ``string`` and rejected every numeric
        value as ``CORE_TYPE``.
        """
        from schemas.canonical import CoreFeatures

        unknown = sorted(set(self.approved_core_keys) - set(CoreFeatures.model_fields))
        if unknown:
            raise ValueError(
                f"approved_core_keys not in the CoreFeatures union: {unknown} "
                "(add the key to schemas/canonical.py first — docs/onboarding.md step 4)"
            )
        untyped = sorted(set(self.approved_core_keys) - set(self.core_key_types))
        if untyped:
            raise ValueError(f"core_key_types must declare a type for: {untyped}")
        return self


class MappingConfig(BaseModel):
    """A human-confirmed mapping, persisted as a deterministic config (architecture §1.5/§1.6)."""

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

"""LLM-assisted mapping report schema (architecture §1.6, ROADMAP Task 1.1).

The LLM produces this report — never a direct transformation. After human
confirmation it is persisted as a deterministic mapping configuration.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: Kind of a deployment-declared model feature (architecture §1.3a): numbers
#: enter the model as-is, categories are one-hot encoded by Node 2.
FeatureKind = Literal["number", "category"]

#: Key of a declared model feature: snake_case, letter first, at most 48 chars.
FEATURE_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,47}$"


class SourceFingerprint(BaseModel):
    """Data-shape signature used by the router (§1.6 fingerprint rule)."""

    model_config = ConfigDict(extra="forbid")

    headers_hash: str = Field(..., min_length=64, max_length=64)
    sheet_names: list[str] = Field(default_factory=list)
    # The workbook sheet that holds the data (``column_names`` describe it); None
    # for a single table and for mappings confirmed before it was recorded.
    primary_sheet: str | None = None
    column_names: list[str] = Field(default_factory=list)
    sample_dtypes: dict[str, str] = Field(default_factory=dict)
    n_sample_rows: int = Field(..., ge=0)


class ProposedMapping(BaseModel):
    """One source-column → canonical-field mapping proposal."""

    model_config = ConfigDict(extra="forbid")

    source_column: str
    target_field: str
    confidence: float = Field(..., ge=0, le=1)
    transformation: str
    notes: str | None = None
    # Only for ``feature.<key>`` targets (§1.3a): how the model should read the
    # column. A proposal, never an approval — see ``MappingConfig.approved_features``.
    feature_kind: FeatureKind | None = Field(default=None, exclude_if=lambda v: v is None)


class SuggestedExtraFeature(BaseModel):
    """A suggested storage-only feature (never auto-promoted to modeling)."""

    model_config = ConfigDict(extra="forbid")

    source: str
    suggested_key: str


class MappingReport(BaseModel):
    """Exact mapping report schema (§1.6)."""

    model_config = ConfigDict(extra="forbid")

    source_fingerprint: SourceFingerprint
    proposed_mappings: list[ProposedMapping]
    unmapped_columns: list[str] = Field(default_factory=list)
    suggested_extra_features: list[SuggestedExtraFeature] = Field(default_factory=list)
    data_quality_flags: list[str] = Field(default_factory=list)
    recommended_action: str
    llm_model_used: str
    generated_at: datetime


class FeatureScreening(BaseModel):
    """Deterministic screening of one candidate model feature (architecture §1.8a).

    ``verdict`` is ``block`` (cannot be approved), ``warn`` (the human decides)
    or ``ok``. Computed by ``router.feature_screening``, never by the LLM.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., pattern=FEATURE_KEY_PATTERN)
    source_column: str
    kind: FeatureKind
    verdict: Literal["ok", "warn", "block"]
    block_reasons: list[str] = Field(default_factory=list)
    warn_reasons: list[str] = Field(default_factory=list)
    info: list[str] = Field(default_factory=list)
    n_evaluable: int = Field(..., ge=0)
    n_present: int = Field(..., ge=0)
    missing_fraction: float = Field(..., ge=0, le=1)
    n_levels: int | None = None
    auc: float | None = None
    presence_gap: float = Field(..., ge=0, le=1)
    tenure_correlation: float | None = None
    direction: Literal["higher_more_churn", "higher_less_churn"] | None = None
    # Smallest proportional-hazards test p-value of a Cox fit on this feature
    # alone (None when not computed or not fittable).
    ph_p_value: float | None = None
    screening_version: str


class ApprovedFeature(BaseModel):
    """A model feature a human approved at mapping confirmation (§1.3a).

    Stores the screening snapshot the approval was made against, so the
    decision stays auditable after thresholds change.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., pattern=FEATURE_KEY_PATTERN)
    kind: FeatureKind
    source_column: str
    label: str
    screening: FeatureScreening

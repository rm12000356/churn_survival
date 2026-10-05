from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FeatureKind = Literal["number", "category"]

FEATURE_KEY_PATTERN = r"^[a-z][a-z0-9_]{0,47}$"


class SourceFingerprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headers_hash: str = Field(..., min_length=64, max_length=64)
    sheet_names: list[str] = Field(default_factory=list)
    primary_sheet: str | None = None
    column_names: list[str] = Field(default_factory=list)
    sample_dtypes: dict[str, str] = Field(default_factory=dict)
    n_sample_rows: int = Field(..., ge=0)


class ProposedMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_column: str
    target_field: str
    confidence: float = Field(..., ge=0, le=1)
    transformation: str
    notes: str | None = None
    feature_kind: FeatureKind | None = Field(default=None, exclude_if=lambda v: v is None)


class SuggestedExtraFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    suggested_key: str


class MappingReport(BaseModel):
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
    ph_p_value: float | None = None
    screening_version: str


class ApprovedFeature(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(..., pattern=FEATURE_KEY_PATTERN)
    kind: FeatureKind
    source_column: str
    label: str
    screening: FeatureScreening

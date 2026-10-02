"""LLM-assisted mapping report schema (architecture §1.6, ROADMAP Task 1.1).

The LLM produces this report — never a direct transformation. After human
confirmation it is persisted as a deterministic mapping configuration.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


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

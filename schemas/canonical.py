from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CoreFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_tier: str | None = None
    contract_length_months: float | None = None
    usage_frequency: float | None = None
    support_tickets_90d: float | None = None
    contract: str | None = None
    internet_service: str | None = None
    monthly_charges: float | None = None
    senior_citizen: float | None = None


class CanonicalRecordMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_adapter: str
    mapping_version: str
    ingested_at: datetime
    original_row_id: str | None = None
    reference_date: date


class CanonicalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(..., min_length=1)
    observation_start: date
    observation_end: date
    event_observed: Literal[0, 1]
    tenure: float
    core_features: CoreFeatures
    extra_features: dict[str, Any] = Field(default_factory=dict)
    model_features: dict[str, float | str | None] = Field(
        default_factory=dict, exclude_if=lambda value: not value
    )
    meta: CanonicalRecordMeta

    @model_validator(mode="after")
    def _validate_observation_window(self) -> Self:
        if self.observation_start > self.observation_end:
            raise ValueError(
                f"observation_start ({self.observation_start.isoformat()}) must be "
                f"<= observation_end ({self.observation_end.isoformat()})"
            )
        expected = float((self.observation_end - self.observation_start).days)
        if self.tenure != expected:
            raise ValueError(
                f"tenure ({self.tenure}) must equal "
                f"(observation_end - observation_start).days ({expected})"
            )
        return self

    @model_validator(mode="after")
    def _model_features_disjoint_from_core(self) -> Self:
        clash = sorted(set(self.model_features) & set(CoreFeatures.model_fields))
        if clash:
            raise ValueError(f"model_features keys clash with core feature names: {clash}")
        return self

    @model_validator(mode="after")
    def _validate_tenure(self) -> Self:
        if not math.isfinite(self.tenure) or self.tenure < 0:
            raise ValueError(f"tenure must be finite and >= 0, got {self.tenure!r}")
        return self

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from schemas.canonical import CanonicalRecord
from schemas.enums import ValidationStatus


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ValidationStatus
    n_input_rows: int = Field(..., ge=0)
    n_accepted: int = Field(..., ge=0)
    n_rejected: int = Field(..., ge=0)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    demoted_features: dict[str, int] = Field(
        default_factory=dict,
        description="Core keys moved to extra_features by the feature gate (§1.8): "
        "key -> number of records demoted",
    )
    missingness_passthrough: dict[str, int] = Field(
        default_factory=dict,
        description="Approved core keys passed through as null cores (§1.7 amendment, "
        "dataset7 v1.2): key -> number of records in which the value was missing "
        "within threshold and accepted with a null core (Node 2 complete-case "
        "excludes them from the model matrix)",
    )
    adapter_used: str
    matched_candidates: list[str] = Field(default_factory=list)
    mapping_version: str
    reference_date: date


class Node1Output(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_dataset: list[CanonicalRecord] = Field(default_factory=list)
    validation_report: ValidationReport

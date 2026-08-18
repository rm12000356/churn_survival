"""Node 1 output contract (architecture §1.2, ROADMAP Task 1.1)."""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from schemas.canonical import CanonicalRecord
from schemas.enums import ValidationStatus


class ValidationReport(BaseModel):
    """Exact structure Node 1 must return (§1.2)."""

    model_config = ConfigDict(extra="forbid")

    status: ValidationStatus
    n_input_rows: int = Field(..., ge=0)
    n_accepted: int = Field(..., ge=0)
    n_rejected: int = Field(..., ge=0)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    adapter_used: str
    mapping_version: str
    reference_date: date


class Node1Output(BaseModel):
    """Node 1 pipeline output: validated records + validation report (§1.2)."""

    model_config = ConfigDict(extra="forbid")

    canonical_dataset: list[CanonicalRecord] = Field(default_factory=list)
    validation_report: ValidationReport

"""API request/response DTOs (ROADMAP Phase 8, Task 8.2).

Domain contracts (run summaries, node outputs, mapping reports) live in
``schemas/`` and are reused directly; only request envelopes and small response
envelopes are defined here.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from schemas.mapping import MappingReport, SourceFingerprint


class RunTriggerRequest(BaseModel):
    """Body of ``POST /runs`` — the only endpoint that triggers computation."""

    model_config = ConfigDict(extra="forbid")

    raw_path: str
    node1_version: str = "1"
    node2_version: str = "1"
    node3_version: str = "1"
    node4_version: str = "1"
    node5_version: str = "1"
    action_rules_version: str = "1"
    reference_date: date | None = None
    support_data: list[dict[str, Any]] | None = None
    persist_artifact: bool = False
    # Optional UI/audit linkage: the run this one supersedes (e.g. a stopped
    # run whose mapping was just confirmed). Does not reconstruct inputs.
    supersedes_run_id: str | None = None


class MappingDraftRequest(BaseModel):
    """Body of ``POST /mappings/draft``."""

    model_config = ConfigDict(extra="forbid")

    raw_path: str
    use_llm: bool = False


class MappingConfirmRequest(BaseModel):
    """Body of ``POST /mappings/confirm``.

    The caller posting this *is* the human decision; it is routed through the
    orchestrator's mapping gate, never applied directly.
    """

    model_config = ConfigDict(extra="forbid")

    report: MappingReport
    fingerprint: SourceFingerprint | None = None
    raw_path: str | None = None
    confirmed_by: str | None = None


class MappingConfirmResponse(BaseModel):
    """Result of persisting a human-confirmed mapping."""

    model_config = ConfigDict(extra="forbid")

    mapping_version: str
    fingerprint: SourceFingerprint


class HealthResponse(BaseModel):
    """``GET /health``."""

    model_config = ConfigDict(extra="forbid")

    status: str
    service: str
    writes_enabled: bool
    reference_date: date


class ModelListResponse(BaseModel):
    """``GET /models``."""

    model_config = ConfigDict(extra="forbid")

    models: list[str] = Field(default_factory=list)
    total: int = Field(..., ge=0)

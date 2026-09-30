"""API request/response DTOs (ROADMAP Phase 8, Task 8.2).

Domain contracts (run summaries, node outputs, mapping reports) live in
``schemas/`` and are reused directly; only request envelopes and small response
envelopes are defined here.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.mapping import MappingReport, SourceFingerprint


class RunTriggerRequest(BaseModel):
    """Body of ``POST /runs`` — the only endpoint that triggers computation."""

    model_config = ConfigDict(extra="forbid")

    raw_path: str
    node1_version: str = "auto"
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
    node1_config_version: str | None = None


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


class Node1ConfigInfo(BaseModel):
    """One available Node 1 deployment config (``config/node1/v<version>.json``)."""

    model_config = ConfigDict(extra="forbid")

    version: str
    approved_core_keys: list[str] = Field(default_factory=list)
    allow_missing_core_passthrough: bool = False


class Node1ConfigListResponse(BaseModel):
    """``GET /node1-configs``."""

    model_config = ConfigDict(extra="forbid")

    configs: list[Node1ConfigInfo] = Field(default_factory=list)
    total: int = Field(..., ge=0)


class RawFileInfo(BaseModel):
    """One raw data file available under ``RAW_DATA_DIR`` (``GET /raw-files``).

    ``kind`` distinguishes the two roles a file can play:
    ``"dataset"`` (a Node 1 customer dataset: CSV/Excel) vs ``"support"``
    (a Node 3 support-threads JSON). Files that are neither are not listed.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    raw_path: str
    kind: Literal["dataset", "support"]
    size_bytes: int = Field(..., ge=0)
    modified_at: datetime


class RawFileListResponse(BaseModel):
    """``GET /raw-files``."""

    model_config = ConfigDict(extra="forbid")

    files: list[RawFileInfo] = Field(default_factory=list)
    total: int = Field(..., ge=0)


class UploadResponse(BaseModel):
    """``POST /uploads`` — a raw file now available server-side."""

    model_config = ConfigDict(extra="forbid")

    filename: str
    raw_path: str
    kind: Literal["dataset", "support"]
    size_bytes: int = Field(..., ge=0)

"""API request/response DTOs (ROADMAP Phase 8, Task 8.2).

Domain contracts (run summaries, node outputs, mapping reports) live in
``schemas/`` and are reused directly; only request envelopes and small response
envelopes are defined here.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.mapping import MappingReport, SourceFingerprint

# Config versions become file-name fragments (``v<version>.json``): plain names only.
VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
# Run ids / model versions are content hashes; never paths.
ID_PATTERN = r"^[A-Za-z0-9_-]{1,128}$"
#: Upper bound on inline support threads per run (dataset 7 ships ~5.7k).
MAX_SUPPORT_THREADS = 200_000

VersionStr = Annotated[str, Field(pattern=VERSION_PATTERN)]


class RunTriggerRequest(BaseModel):
    """Body of ``POST /runs`` — the only endpoint that triggers computation."""

    model_config = ConfigDict(extra="forbid")

    raw_path: str = Field(..., min_length=1, max_length=4096)
    node1_version: VersionStr = "auto"
    node2_version: VersionStr = "1"
    node3_version: VersionStr = "1"
    node4_version: VersionStr = "4"
    node5_version: VersionStr = "1"
    action_rules_version: VersionStr = "1"
    reference_date: date | None = None
    support_data: list[dict[str, Any]] | None = Field(
        default=None, max_length=MAX_SUPPORT_THREADS
    )
    persist_artifact: bool = False
    # Optional LLM use, off by default (template-only, offline extraction). Each
    # requires a server-side LLM (LLM_PROVIDER != none) and is part of run_id.
    llm_node3: bool = False  # Node 3 support-thread extraction
    llm_node5: bool = False  # Node 5 explanation polish
    # Optional UI/audit linkage: the run this one supersedes (e.g. a stopped
    # run whose mapping was just confirmed). Does not reconstruct inputs.
    supersedes_run_id: str | None = Field(default=None, pattern=ID_PATTERN)


class MappingDraftRequest(BaseModel):
    """Body of ``POST /mappings/draft``."""

    model_config = ConfigDict(extra="forbid")

    raw_path: str = Field(..., min_length=1, max_length=4096)
    use_llm: bool = False


class MappingConfirmRequest(BaseModel):
    """Body of ``POST /mappings/confirm``.

    The caller posting this *is* the human decision; it is routed through the
    orchestrator's mapping gate, never applied directly.
    """

    model_config = ConfigDict(extra="forbid")

    report: MappingReport
    fingerprint: SourceFingerprint | None = None
    raw_path: str | None = Field(default=None, min_length=1, max_length=4096)
    confirmed_by: str | None = Field(default=None, min_length=1, max_length=120)
    node1_config_version: str | None = Field(default=None, pattern=VERSION_PATTERN)


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
    # Whether runs may opt into the LLM (LLM_PROVIDER configured), and its model.
    llm_available: bool = False
    llm_model: str | None = None


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

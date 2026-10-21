from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from schemas.mapping import FeatureScreening, MappingReport, SourceFingerprint

VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
ID_PATTERN = r"^[A-Za-z0-9_-]{1,128}$"
MAX_SUPPORT_THREADS = 200_000

VersionStr = Annotated[str, Field(pattern=VERSION_PATTERN)]


class RunTriggerRequest(BaseModel):
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
    llm_node3: bool = False
    llm_node5: bool = False
    supersedes_run_id: str | None = Field(default=None, pattern=ID_PATTERN)


class MappingDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_path: str = Field(..., min_length=1, max_length=4096)
    use_llm: bool = False


class MappingCandidatesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report: MappingReport
    raw_path: str = Field(..., min_length=1, max_length=4096)


class MappingCandidatesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    screening_version: str
    n_evaluable: int
    candidates: list[FeatureScreening]


class MappingConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report: MappingReport
    fingerprint: SourceFingerprint | None = None
    raw_path: str | None = Field(default=None, min_length=1, max_length=4096)
    confirmed_by: str | None = Field(default=None, min_length=1, max_length=120)
    node1_config_version: str | None = Field(default=None, pattern=VERSION_PATTERN)
    approved_features: list[str] = Field(default_factory=list, max_length=200)
    supersedes_mapping_version: str | None = Field(default=None, pattern=VERSION_PATTERN)


class MappingConfirmResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mapping_version: str
    fingerprint: SourceFingerprint


class LivenessResponse(BaseModel):
    """Minimal public liveness probe: no server posture is exposed."""

    model_config = ConfigDict(extra="forbid")

    status: str
    service: str


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    service: str
    writes_enabled: bool
    reference_date: date
    llm_available: bool = False
    llm_model: str | None = None


class ModelListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    models: list[str] = Field(default_factory=list)
    total: int = Field(..., ge=0)


class Node1ConfigInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    approved_core_keys: list[str] = Field(default_factory=list)
    allow_missing_core_passthrough: bool = False


class Node1ConfigListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    configs: list[Node1ConfigInfo] = Field(default_factory=list)
    total: int = Field(..., ge=0)


class RawFileInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    raw_path: str
    kind: Literal["dataset", "support"]
    size_bytes: int = Field(..., ge=0)
    modified_at: datetime


class RawFileListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[RawFileInfo] = Field(default_factory=list)
    total: int = Field(..., ge=0)


class UploadResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str
    raw_path: str
    kind: Literal["dataset", "support"]
    size_bytes: int = Field(..., ge=0)

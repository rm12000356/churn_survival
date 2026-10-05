from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from schemas.mapping import SourceFingerprint


class RunExecutionStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    STOPPED_NEEDS_MAPPING = "STOPPED_NEEDS_MAPPING"
    STOPPED_VALIDATION = "STOPPED_VALIDATION"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class RoutingIdentitySource(StrEnum):
    COMPUTED = "computed"
    UNKNOWN_PRE_MIGRATION = "unknown_pre_migration"


class RoutingIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    matched: bool
    adapter: str | None = None
    adapter_version: str | None = None
    confidence: float | None = None

    @classmethod
    def no_match(cls) -> RoutingIdentity:
        return cls(matched=False)


class RunLinks(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: str
    report: str
    report_html: str
    ranked_accounts: str


class RunSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    execution_status: RunExecutionStatus = RunExecutionStatus.PENDING
    pipeline_status: str | None = None
    stage: str | None = None

    raw_path: str | None = None
    raw_digest: str | None = None
    reference_date: date | None = None

    adapter_used: str | None = None
    n_accepted: int | None = None
    n_rejected: int | None = None

    model_type: str | None = None
    model_version: str | None = None
    n_ranked: int | None = None
    n_insufficient: int | None = None
    n_reports: int | None = None
    mapping_version: str | None = None
    superseded_by: str | None = None

    routing_identity_source: RoutingIdentitySource = RoutingIdentitySource.COMPUTED
    routing_adapter: str | None = None
    routing_adapter_version: str | None = None
    routing_confidence: float | None = None

    error_code: str | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    pending_fingerprint: SourceFingerprint | None = None

    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RunListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runs: list[RunSummary] = Field(default_factory=list)
    total: int = Field(..., ge=0)


class RunCreatedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    execution_status: RunExecutionStatus
    links: RunLinks

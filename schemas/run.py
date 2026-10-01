"""Run persistence & execution contracts (ROADMAP Phase 8, architecture §8.5).

These models describe a pipeline *run* as stored by the Phase 8 persistence
layer and served by the Phase 8 API. A run is identified by a content-addressed
``run_id`` whose inputs include the raw file digest, the support-input digest,
every versioned config, the declared ``reference_date`` and the resolved
``routing_identity`` — so the mapping registry (which adapter transforms the raw
bytes) is part of the computation's identity. Same identity -> same output.

``RunExecutionStatus`` is the *execution lifecycle* and is deliberately separate
from the pipeline's own terminal ``PipelineStatus`` (the graph outcome); a run
can be ``RUNNING`` long before any pipeline status exists.

Nothing here contains wall-clock time as a decision input: ``created_at`` /
``started_at`` / ``finished_at`` are operational bookkeeping only and never enter
the deterministic report/state outputs.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from schemas.mapping import SourceFingerprint


class RunExecutionStatus(StrEnum):
    """Execution lifecycle of a persisted run (Phase 8, D-P5)."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    STOPPED_NEEDS_MAPPING = "STOPPED_NEEDS_MAPPING"
    STOPPED_VALIDATION = "STOPPED_VALIDATION"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"


class RoutingIdentitySource(StrEnum):
    """How a run's routing identity was obtained.

    ``UNKNOWN_PRE_MIGRATION`` is surfaced explicitly (never an empty string that
    could read as valid data) for rows that predate routing-inclusive ids and
    were self-healed from disk.
    """

    COMPUTED = "computed"
    UNKNOWN_PRE_MIGRATION = "unknown_pre_migration"


class RoutingIdentity(BaseModel):
    """The deterministic router outcome that is part of a run's identity.

    Decision-free: it records which adapter the *router* selected (or that none
    matched). It never encodes a risk level, score, or any downstream decision.
    """

    model_config = ConfigDict(extra="forbid")

    matched: bool
    adapter: str | None = None
    adapter_version: str | None = None
    confidence: float | None = None

    @classmethod
    def no_match(cls) -> RoutingIdentity:
        return cls(matched=False)


class RunLinks(BaseModel):
    """Hypermedia links for a run (Phase 8 API)."""

    model_config = ConfigDict(extra="forbid")

    detail: str
    report: str
    report_html: str
    ranked_accounts: str


class RunSummary(BaseModel):
    """A run's queryable metadata — index row + API list item (architecture §8.5).

    Serves as both the SQLite index row contract and the API representation, so
    there is a single source of truth for what a run's metadata means.
    """

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
    """Response for ``GET /runs`` (Phase 8 API)."""

    model_config = ConfigDict(extra="forbid")

    runs: list[RunSummary] = Field(default_factory=list)
    total: int = Field(..., ge=0)


class RunCreatedResponse(BaseModel):
    """Response for ``POST /runs`` — enqueued/served run reference (Phase 8 API)."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    execution_status: RunExecutionStatus
    links: RunLinks

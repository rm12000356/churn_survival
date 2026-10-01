"""Pipeline orchestration (ROADMAP Phase 7 + Phase 8).

A deterministic, plain-Python equivalent of the LangGraph orchestration described
in architecture §6.5/§8.3. Statistical work stays in the node packages; this layer
only routes, gates human mapping confirmation, sequences Node 1 -> Node 5, and
(Phase 8) persists runs and serves run metadata/GC.
"""

from __future__ import annotations

from orchestration.graph import resume_pipeline, run_pipeline
from orchestration.identity import compute_run_id, compute_support_digest, sha256_file
from orchestration.index import RunIndex
from orchestration.mapping import (
    CallbackMappingGate,
    MappingGate,
    persist_confirmed_mapping,
)
from orchestration.persistence import RunStore, compute_trigger_run_id
from orchestration.state import (
    PipelineResult,
    PipelineStage,
    PipelineState,
    PipelineStatus,
)

__all__ = [
    "CallbackMappingGate",
    "MappingGate",
    "PipelineResult",
    "PipelineStage",
    "PipelineState",
    "PipelineStatus",
    "RunIndex",
    "RunStore",
    "compute_run_id",
    "compute_support_digest",
    "compute_trigger_run_id",
    "persist_confirmed_mapping",
    "resume_pipeline",
    "run_pipeline",
    "sha256_file",
]

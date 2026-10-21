from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from churn_io.atomic import atomic_write
from schemas.mapping import MappingReport, SourceFingerprint
from schemas.node2 import Node2Output
from schemas.node3 import Node3Output
from schemas.node4 import Node4Output
from schemas.node5 import Node5Output
from schemas.run import RoutingIdentity
from schemas.validation import Node1Output


class PipelineStatus(StrEnum):
    COMPLETED = "COMPLETED"
    STOPPED_NEEDS_MAPPING = "STOPPED_NEEDS_MAPPING"
    STOPPED_VALIDATION = "STOPPED_VALIDATION"
    FAILED = "FAILED"


class PipelineStage(StrEnum):
    ROUTING = "routing"
    MAPPING_CONFIRMATION = "mapping_confirmation"
    NODE1 = "node1"
    NODE2 = "node2"
    NODE3 = "node3"
    NODE4 = "node4"
    NODE5 = "node5"
    DONE = "done"


def _dump(model: BaseModel | None) -> Any:
    return None if model is None else model.model_dump(mode="json")


def _load[T: BaseModel](model: type[T], value: Any) -> T | None:
    if value is None:
        return None
    if isinstance(value, model):
        return value
    return model.model_validate(value)


@dataclass
class PipelineState:
    raw_path: str
    reference_date: date
    stage: PipelineStage = PipelineStage.ROUTING
    status: PipelineStatus | None = None
    config_versions: dict[str, str] = field(default_factory=dict)
    run_id: str | None = None
    raw_digest: str | None = None
    support_digest: str | None = None
    routing_identity: RoutingIdentity | None = None
    fingerprint: SourceFingerprint | None = None
    routing: dict[str, Any] | None = None
    matched_candidates: list[str] = field(default_factory=list)
    mapping_report: MappingReport | None = None
    mapping_version: str | None = None
    node1_output: Node1Output | None = None
    node2_output: Node2Output | None = None
    node3_output: Node3Output | None = None
    node4_output: Node4Output | None = None
    node5_output: Node5Output | None = None
    artifact_dir: str | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_path": self.raw_path,
            "reference_date": self.reference_date.isoformat(),
            "stage": self.stage.value,
            "status": self.status.value if self.status is not None else None,
            "config_versions": dict(self.config_versions),
            "run_id": self.run_id,
            "raw_digest": self.raw_digest,
            "support_digest": self.support_digest,
            "routing_identity": _dump(self.routing_identity),
            "fingerprint": _dump(self.fingerprint),
            "routing": self.routing,
            "matched_candidates": list(self.matched_candidates),
            "mapping_report": _dump(self.mapping_report),
            "mapping_version": self.mapping_version,
            "node1_output": _dump(self.node1_output),
            "node2_output": _dump(self.node2_output),
            "node3_output": _dump(self.node3_output),
            "node4_output": _dump(self.node4_output),
            "node5_output": _dump(self.node5_output),
            "artifact_dir": self.artifact_dir,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PipelineState:
        status = data.get("status")
        return cls(
            raw_path=str(data["raw_path"]),
            reference_date=date.fromisoformat(data["reference_date"]),
            stage=PipelineStage(data.get("stage", PipelineStage.ROUTING.value)),
            status=PipelineStatus(status) if status else None,
            config_versions=dict(data.get("config_versions") or {}),
            run_id=data.get("run_id"),
            raw_digest=data.get("raw_digest"),
            support_digest=data.get("support_digest"),
            routing_identity=_load(RoutingIdentity, data.get("routing_identity")),
            fingerprint=_load(SourceFingerprint, data.get("fingerprint")),
            routing=data.get("routing"),
            matched_candidates=list(data.get("matched_candidates") or []),
            mapping_report=_load(MappingReport, data.get("mapping_report")),
            mapping_version=data.get("mapping_version"),
            node1_output=_load(Node1Output, data.get("node1_output")),
            node2_output=_load(Node2Output, data.get("node2_output")),
            node3_output=_load(Node3Output, data.get("node3_output")),
            node4_output=_load(Node4Output, data.get("node4_output")),
            node5_output=_load(Node5Output, data.get("node5_output")),
            artifact_dir=data.get("artifact_dir"),
            warnings=list(data.get("warnings") or []),
            errors=list(data.get("errors") or []),
        )


@dataclass
class PipelineResult:
    state: PipelineState

    @property
    def status(self) -> PipelineStatus | None:
        return self.state.status

    @property
    def completed(self) -> bool:
        return self.state.status == PipelineStatus.COMPLETED

    def to_dict(self) -> dict[str, Any]:
        return self.state.to_dict()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PipelineResult:
        return cls(state=PipelineState.from_dict(data))

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(target, json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n")
        return target

    @classmethod
    def load(cls, path: str | Path) -> PipelineResult:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def summary(self) -> dict[str, Any]:
        state = self.state
        node1 = state.node1_output
        node4 = state.node4_output
        node5 = state.node5_output
        return {
            "status": state.status.value if state.status is not None else None,
            "stage": state.stage.value,
            "raw_path": state.raw_path,
            "reference_date": state.reference_date.isoformat(),
            "adapter_used": node1.validation_report.adapter_used if node1 else None,
            "n_accepted": node1.validation_report.n_accepted if node1 else None,
            "n_rejected": node1.validation_report.n_rejected if node1 else None,
            "model_type": state.node2_output.model_type.value if state.node2_output else None,
            "model_version": state.node2_output.model_version if state.node2_output else None,
            "n_ranked": len(node4.ranked_accounts) if node4 else None,
            "n_insufficient": len(node4.insufficient_data_accounts) if node4 else None,
            "n_reports": (
                len(node5.report.priority_accounts)
                + len(node5.report.insufficient_data_accounts)
                if node5
                else None
            ),
            "mapping_version": state.mapping_version,
            "artifact_dir": state.artifact_dir,
            "warnings": list(state.warnings),
            "errors": list(state.errors),
        }

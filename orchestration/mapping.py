from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from config.models import MappingConfig
from schemas.mapping import ApprovedFeature, MappingReport, SourceFingerprint

__all__ = ["CallbackMappingGate", "MappingGate", "persist_confirmed_mapping"]


class MappingGate(Protocol):
    name: str

    def confirm(
        self, report: MappingReport, fingerprint: SourceFingerprint
    ) -> MappingReport | None:
        ...


@dataclass
class CallbackMappingGate:
    callback: Callable[[MappingReport, SourceFingerprint], MappingReport | None]
    name: str = field(default="human")

    def confirm(
        self, report: MappingReport, fingerprint: SourceFingerprint
    ) -> MappingReport | None:
        return self.callback(report, fingerprint)


def persist_confirmed_mapping(
    report: MappingReport,
    *,
    config_dir: str | Path,
    confirmed_by: str,
    confirmed_at: datetime | None = None,
    node1_config_version: str | None = None,
    approved_features: Sequence[ApprovedFeature] = (),
    supersedes: str | None = None,
) -> MappingConfig:
    from router.llm_mapper import confirm_and_persist

    return confirm_and_persist(
        report,
        config_dir=Path(config_dir),
        confirmed_by=confirmed_by,
        confirmed_at=confirmed_at,
        node1_config_version=node1_config_version,
        approved_features=approved_features,
        supersedes=supersedes,
    )

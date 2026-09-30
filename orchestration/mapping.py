"""Human mapping-confirmation gate (architecture §1.6, ROADMAP Task 7.1).

The gate is the *only* way a mapping becomes configuration. It never runs an
LLM: it receives a completed ``MappingReport`` (drafted manually or by the Node 1
mapping workflow) and returns the human-approved report, or ``None`` to decline.

Two documented modes:

- **Same-process (CLI):** a ``CallbackMappingGate`` supplies the human answer in
  the same call.
- **Cross-request (web, Phase 8):** no gate is passed; the run stops with
  ``STOPPED_NEEDS_MAPPING`` and a serialized ``PipelineResult``. A later request
  confirms the report through this gate and re-enters the pipeline at routing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol

from config.models import MappingConfig
from schemas.mapping import MappingReport, SourceFingerprint

__all__ = ["CallbackMappingGate", "MappingGate", "persist_confirmed_mapping"]


class MappingGate(Protocol):
    """A human decision on a drafted mapping report."""

    name: str

    def confirm(
        self, report: MappingReport, fingerprint: SourceFingerprint
    ) -> MappingReport | None:
        """Return the approved report, or ``None`` to decline the mapping."""
        ...


@dataclass
class CallbackMappingGate:
    """Adapters a plain function into the ``MappingGate`` protocol.

    The callback must represent a *human* decision; the graph never fabricates
    one. Returning the (possibly edited) report approves it; returning ``None``
    declines and the run stops for later resumption.
    """

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
) -> MappingConfig:
    """Persist a human-approved report as a deterministic adapter (§1.6).

    Route through :func:`router.llm_mapper.confirm_and_persist` so the audited
    transformation whitelist + strict validation apply unchanged. Phase 8 code
    must call this (via the gate), never ``confirm_and_persist`` directly.
    """
    from router.llm_mapper import confirm_and_persist

    return confirm_and_persist(
        report,
        config_dir=Path(config_dir),
        confirmed_by=confirmed_by,
        confirmed_at=confirmed_at,
        node1_config_version=node1_config_version,
    )

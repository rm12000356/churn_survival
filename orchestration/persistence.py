"""Run/report persistence (ROADMAP Phase 8, architecture §8.5, D-P1).

A run is persisted under ``runs/<run_id>/``:

- ``state.json``   — the full round-trippable ``PipelineResult``
- ``summary.json`` — the queryable ``RunSummary`` metadata
- ``node1.json`` … ``node4.json`` — each completed node output (when present)
- ``node5.json``   — the client-facing report object
- ``report.html``  — the dependency-free HTML rendering

``run_id`` is content-addressed over the *full* computation identity (including
the resolved routing decision), so the same inputs + config + mapping registry
always produce the same directory. Persistence is a caller concern: ``run_pipeline``
stays pure and never writes here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from orchestration.identity import (
    compute_run_id,
    compute_support_digest,
    sha256_file,
)
from orchestration.index import RunIndex, _log
from orchestration.routing import route_input, routing_identity
from orchestration.state import PipelineResult, PipelineStatus
from schemas.run import (
    RoutingIdentity,
    RoutingIdentitySource,
    RunExecutionStatus,
    RunSummary,
)

__all__ = ["RunStore", "build_summary", "compute_trigger_run_id"]

_NODE_NAMES = ("node1", "node2", "node3", "node4", "node5")
#: Output files a run directory may hold (state/summary are always rewritten).
_OUTPUT_FILES = frozenset({f"{node}.json" for node in _NODE_NAMES} | {"report.html"})
#: Run ids are content hashes; anything else (dots, separators) is refused.
_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to a sibling temp file, then atomically replace ``path``."""
    temp: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            temp = Path(handle.name)
            handle.write(text)
        os.replace(temp, path)
    except BaseException:
        if temp is not None:
            temp.unlink(missing_ok=True)
        raise


def _read_summary(path: Path) -> RunSummary | None:
    """Load a ``summary.json`` sidecar; ``None`` if absent or unreadable."""
    if not path.is_file():
        return None
    try:
        return RunSummary.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _dump_state(result: PipelineResult) -> str:
    """Same bytes ``PipelineResult.save`` writes."""
    return json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n"


def _execution_status(status: PipelineStatus | None) -> RunExecutionStatus:
    if status == PipelineStatus.COMPLETED:
        return RunExecutionStatus.COMPLETED
    if status == PipelineStatus.STOPPED_NEEDS_MAPPING:
        return RunExecutionStatus.STOPPED_NEEDS_MAPPING
    if status == PipelineStatus.STOPPED_VALIDATION:
        return RunExecutionStatus.STOPPED_VALIDATION
    if status == PipelineStatus.FAILED:
        return RunExecutionStatus.FAILED
    return RunExecutionStatus.RUNNING


def build_summary(result: PipelineResult, *, legacy: bool = False) -> RunSummary:
    """Assemble a ``RunSummary`` from a completed/stopped/failed result.

    ``legacy=True`` is used only when self-healing rows that predate
    routing-inclusive ids: routing fields stay null and the source is flagged
    ``unknown_pre_migration`` so the API never presents an empty string as valid.
    """
    state = result.state
    exec_status = _execution_status(state.status)
    node1 = state.node1_output
    node2 = state.node2_output
    node4 = state.node4_output
    node5 = state.node5_output

    ri: RoutingIdentity | None = None if legacy else state.routing_identity
    source = (
        RoutingIdentitySource.UNKNOWN_PRE_MIGRATION
        if legacy
        else RoutingIdentitySource.COMPUTED
    )

    error_code = None
    if exec_status == RunExecutionStatus.FAILED and state.errors:
        error_code = state.errors[0].get("code")

    pending_fingerprint = (
        state.fingerprint
        if exec_status == RunExecutionStatus.STOPPED_NEEDS_MAPPING
        else None
    )

    return RunSummary(
        run_id=state.run_id or "",
        execution_status=exec_status,
        pipeline_status=state.status.value if state.status is not None else None,
        stage=state.stage.value,
        raw_path=state.raw_path,
        raw_digest=state.raw_digest,
        reference_date=state.reference_date,
        adapter_used=node1.validation_report.adapter_used if node1 is not None else None,
        n_accepted=node1.validation_report.n_accepted if node1 is not None else None,
        n_rejected=node1.validation_report.n_rejected if node1 is not None else None,
        model_type=node2.model_type.value if node2 is not None else None,
        model_version=node2.model_version if node2 is not None else None,
        n_ranked=len(node4.ranked_accounts) if node4 is not None else None,
        n_insufficient=(
            len(node4.insufficient_data_accounts) if node4 is not None else None
        ),
        n_reports=(
            len(node5.report.priority_accounts)
            + len(node5.report.insufficient_data_accounts)
            if node5 is not None
            else None
        ),
        mapping_version=state.mapping_version,
        routing_identity_source=source,
        routing_adapter=ri.adapter if ri is not None else None,
        routing_adapter_version=ri.adapter_version if ri is not None else None,
        routing_confidence=ri.confidence if ri is not None else None,
        error_code=error_code,
        warnings=list(state.warnings),
        errors=list(state.errors),
        pending_fingerprint=pending_fingerprint,
    )


def compute_trigger_run_id(
    raw_path: str | Path,
    *,
    node1_config: Any,
    adapters: Sequence[Any] | None,
    config_versions: Mapping[str, str],
    reference_date: date,
    support_data: Sequence[Any] | None = None,
    external_threads: Sequence[Any] | None = None,
    sources_config_version: str | None = None,
    identity_mapping_version: str | None = None,
) -> tuple[str, RoutingIdentity]:
    """Compute a run's identity **before** running it (decision-free).

    Fingerprints + routes only (no node execution) to obtain the routing
    identity, then hashes raw/support digests, config versions and
    ``reference_date``. Used by the API trigger for idempotent dedup.
    """
    _fingerprint, decision = route_input(raw_path, node1_config, adapters=list(adapters or []))
    ri = routing_identity(decision)
    raw_digest = sha256_file(raw_path)
    support_digest = compute_support_digest(
        support_data=support_data,
        external_threads=external_threads,
        sources_config_version=sources_config_version,
        identity_mapping_version=identity_mapping_version,
    )
    run_id = compute_run_id(
        raw_digest=raw_digest,
        support_digest=support_digest,
        config_versions=config_versions,
        reference_date=reference_date,
        routing_identity=ri,
    )
    return run_id, ri


class RunStore:
    """File-based run store with a thin SQLite metadata index."""

    def __init__(self, base_dir: str | Path, index: RunIndex | None = None) -> None:
        self.base_dir = Path(base_dir)
        self.index = index if index is not None else RunIndex(self.base_dir / "index.sqlite")

    # --- paths ---------------------------------------------------------------
    def run_dir(self, run_id: str) -> Path:
        """``base_dir/run_id``; ids are plain names, never paths (no traversal)."""
        if not _SAFE_RUN_ID.match(run_id or ""):
            raise ValueError(f"invalid run id {run_id!r}")
        return self.base_dir / run_id

    def exists(self, run_id: str) -> bool:
        return (self.run_dir(run_id) / "state.json").is_file()

    # --- write ---------------------------------------------------------------
    def save(self, result: PipelineResult) -> Path:
        """Persist all outputs for a result and upsert its index row."""
        state = result.state
        run_id = state.run_id
        if not run_id:
            raise ValueError("cannot persist a run without a computed run_id")
        target = self.run_dir(run_id)
        target.mkdir(parents=True, exist_ok=True)

        # Every file is written atomically (temp + replace), so a reader never
        # sees a truncated file during a re-run, and a crash leaves either the old
        # or the new version.
        _atomic_write(target / "state.json", _dump_state(result))

        outputs: dict[str, str] = {}
        for node in ("node1", "node2", "node3", "node4"):
            output = getattr(state, f"{node}_output")
            if output is not None:
                outputs[f"{node}.json"] = (
                    json.dumps(output.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
                )
        if state.node5_output is not None:
            from node5.rendering.html import render_html
            from node5.rendering.json import render_json

            outputs["node5.json"] = render_json(state.node5_output) + "\n"
            outputs["report.html"] = render_html(state.node5_output)
        for name, text in outputs.items():
            _atomic_write(target / name, text)
        # A re-run that stopped earlier must not keep serving the previous
        # attempt's outputs (e.g. a stale report next to a FAILED status).
        for name in _OUTPUT_FILES - outputs.keys():
            (target / name).unlink(missing_ok=True)

        summary = build_summary(result)
        if self.index is not None:
            existing = self.index.get(run_id)
            if existing is not None:
                # Operational bookkeeping lives in the index, not in the result.
                summary = summary.model_copy(
                    update={
                        field: getattr(existing, field)
                        for field in ("created_at", "started_at", "finished_at", "superseded_by")
                        if getattr(summary, field) is None
                    }
                )
        # summary.json last: its presence marks a complete run directory.
        _atomic_write(target / "summary.json", summary.model_dump_json(indent=2) + "\n")
        if self.index is not None:
            self.index.upsert(summary)
        return target

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        _atomic_write(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    # --- read ----------------------------------------------------------------
    def load(self, run_id: str) -> PipelineResult:
        return PipelineResult.load(self.run_dir(run_id) / "state.json")

    def get_summary(self, run_id: str) -> RunSummary | None:
        if self.index is not None:
            found = self.index.get(run_id)
            if found is not None:
                return found
        summary = _read_summary(self.run_dir(run_id) / "summary.json")
        if summary is not None and self.index is not None:
            self.index.insert_if_missing(summary)
        return summary

    def list_runs(
        self,
        *,
        limit: int | None = None,
        offset: int = 0,
        status: RunExecutionStatus | None = None,
        model_version: str | None = None,
    ) -> list[RunSummary]:
        if self.index is None:
            return []
        # Reconcile when disk holds more runs than the index (a deleted or
        # partially lost index.sqlite). The rebuild is insert-only, so live rows
        # are never overwritten; a filter that matches nothing is not a trigger.
        if self._run_dir_count() > self.index.count():
            self.rebuild_index_from_disk()
        return self.index.list(
            limit=limit, offset=offset, status=status, model_version=model_version
        )

    def count_runs(
        self,
        *,
        status: RunExecutionStatus | None = None,
        model_version: str | None = None,
    ) -> int:
        if self.index is None:
            return 0
        return self.index.count(status=status, model_version=model_version)

    def read_node_output(self, run_id: str, node: str) -> dict[str, Any] | None:
        if node not in _NODE_NAMES:
            raise ValueError(f"unknown node {node!r}")
        path = self.run_dir(run_id) / f"{node}.json"
        if not path.is_file():
            return None
        loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return loaded

    def read_report_html(self, run_id: str) -> str | None:
        path = self.run_dir(run_id) / "report.html"
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def delete(self, run_id: str) -> None:
        """Delete a run: index row first (no ghost row), then the whole directory."""
        target = self.run_dir(run_id)
        if self.index is not None:
            self.index.delete(run_id)
        if target.is_dir():
            shutil.rmtree(target)

    # --- maintenance ---------------------------------------------------------
    def _has_run_dirs(self) -> bool:
        return self._run_dir_count() > 0

    def _run_dir_count(self) -> int:
        if not self.base_dir.is_dir():
            return 0
        return sum(
            1
            for child in self.base_dir.iterdir()
            if child.is_dir() and (child / "state.json").is_file()
        )

    def rebuild_index_from_disk(self) -> int:
        """Re-index run directories missing from the index; returns rows added.

        Insert-only: a row already in the index (which may carry newer status or
        bookkeeping than its ``summary.json``) is never overwritten, and a corrupt
        directory is skipped instead of aborting the rebuild.
        """
        if self.index is None or not self.base_dir.is_dir():
            return 0
        count = 0
        for child in sorted(self.base_dir.iterdir()):
            if not child.is_dir() or not _SAFE_RUN_ID.match(child.name):
                continue
            try:
                summary = _read_summary(child / "summary.json")
                if summary is None and (child / "state.json").is_file():
                    # Legacy run (no summary sidecar): never guess routing identity.
                    summary = build_summary(
                        PipelineResult.load(child / "state.json"), legacy=True
                    )
                    summary = summary.model_copy(
                        update={"run_id": summary.run_id or child.name}
                    )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                # Never silent (REVIEW N-M6): a run that cannot be re-indexed is
                # invisible in the API, so say which one and why.
                _log().warning("run_dir_skipped", run_id=child.name, error=type(exc).__name__)
                continue
            if summary is not None and self.index.insert_if_missing(summary):
                count += 1
        return count

    def mark_stale_running_interrupted(self) -> int:
        if self.index is None:
            return 0
        return self.index.mark_stale_running_interrupted()

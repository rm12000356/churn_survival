"""Retention / GC (ROADMAP Phase 8, D-P6).

Hybrid, deliberately explicit and **manual** — no hidden mutation happens unless
an operator (or a server opting into ``GC_ON_STARTUP``) asks for it:

- **Count-based** for model artifacts and runs (keep the newest N by mtime).
- **TTL-based** for pending states — ``STOPPED_NEEDS_MAPPING`` /
  ``STOPPED_VALIDATION`` / ``INTERRUPTED`` run directories and unconfirmed
  mapping drafts. Confirmed ``config/mappings/map_*.json`` adapters are the
  deterministic routing configuration and are **never** pruned.
- **Recovery**: mark stale ``RUNNING`` rows ``INTERRUPTED`` (single-process
  restart; there is no live worker after a restart).

A limit/TTL of ``0`` disables that rule.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from config.settings import get_settings
from orchestration.persistence import RunStore
from schemas.run import RunExecutionStatus, RunSummary

__all__ = [
    "main",
    "prune_mapping_drafts",
    "prune_model_artifacts",
    "prune_runs",
    "recover_stale_running",
]

_TTL_STATUSES = {
    RunExecutionStatus.STOPPED_NEEDS_MAPPING,
    RunExecutionStatus.STOPPED_VALIDATION,
    RunExecutionStatus.INTERRUPTED,
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _mtime(path: Path) -> float:
    return path.stat().st_mtime


def _delete_tree(path: Path) -> None:
    if path.is_dir():
        for child in path.iterdir():
            if child.is_dir():
                _delete_tree(child)
            else:
                child.unlink()
        path.rmdir()
    elif path.exists():
        path.unlink()


def prune_model_artifacts(
    model_dir: str | Path, max_count: int, *, protected: Iterable[str] = ()
) -> list[Path]:
    """Keep the newest ``max_count`` model artifacts; never delete ``protected`` ones.

    Recency is the sidecar's mtime (rewritten on every save/refit), not the
    directory's (which an in-place overwrite does not touch). ``protected`` holds
    model versions still referenced by retained runs.
    """
    base = Path(model_dir)
    if max_count <= 0 or not base.is_dir():
        return []
    keep = set(protected)
    candidates = [
        child
        for child in base.iterdir()
        if child.is_dir() and (child / "model.json").is_file()
    ]
    candidates.sort(key=lambda child: _mtime(child / "model.json"), reverse=True)
    deleted: list[Path] = []
    for stale in candidates[max_count:]:
        if stale.name in keep:
            continue
        _delete_tree(stale)
        deleted.append(stale)
    return deleted


def prune_mapping_drafts(
    drafts_dir: str | Path, ttl_days: int, *, now: datetime | None = None
) -> list[Path]:
    """Delete unconfirmed mapping drafts older than ``ttl_days``."""
    base = Path(drafts_dir)
    if ttl_days <= 0 or not base.is_dir():
        return []
    cutoff = (now or _utcnow()) - timedelta(days=ttl_days)
    deleted: list[Path] = []
    for child in sorted(base.glob("*.json")):
        mtime = datetime.fromtimestamp(_mtime(child), tz=UTC)
        if mtime < cutoff:
            child.unlink()
            deleted.append(child)
    return deleted


def prune_runs(
    store: RunStore,
    *,
    max_runs: int,
    pending_ttl_days: int,
    now: datetime | None = None,
) -> list[str]:
    """TTL-prune pending/terminal-unproductive runs, then count-prune the rest.

    In-flight runs (``PENDING``/``RUNNING``) are never pruned: a forced re-run
    writes into an existing directory. Index rows without a directory (runs that
    failed before persisting anything) are TTL-collected too.
    """
    deleted: list[str] = []

    if pending_ttl_days > 0:
        cutoff = (now or _utcnow()) - timedelta(days=pending_ttl_days)
        if store.base_dir.is_dir():
            for child in sorted(store.base_dir.iterdir()):
                if not child.is_dir() or not (child / "state.json").is_file():
                    continue
                summary = _summary_or_none(store, child.name)
                if summary is None or summary.execution_status not in _TTL_STATUSES:
                    continue
                if _recency(child, summary) < cutoff:
                    store.delete(child.name)
                    deleted.append(child.name)
        if store.index is not None:
            expired = store.index.list_expired(_TTL_STATUSES | {RunExecutionStatus.FAILED}, cutoff)
            for run_id in expired:
                if run_id in deleted or (store.base_dir / run_id / "state.json").is_file():
                    continue
                store.index.delete(run_id)  # orphan row: no directory to keep
                deleted.append(run_id)

    if max_runs > 0 and store.base_dir.is_dir():
        remaining: list[tuple[datetime, str]] = []
        for child in store.base_dir.iterdir():
            if not child.is_dir() or not (child / "state.json").is_file():
                continue
            summary = _summary_or_none(store, child.name)
            if summary is None:
                # Unreadable metadata (e.g. a flaky synced read) says nothing
                # about the run's status, which may be RUNNING: never prune it
                # on a guess (REVIEW N-M6).
                _gc_log().warning("gc_run_skipped_unreadable", run_id=child.name)
                continue
            if summary.execution_status in _IN_FLIGHT:
                continue
            remaining.append((_recency(child, summary), child.name))
        remaining.sort(reverse=True)
        for _stamp, run_id in remaining[max_runs:]:
            store.delete(run_id)
            deleted.append(run_id)

    return deleted


_IN_FLIGHT = {RunExecutionStatus.PENDING, RunExecutionStatus.RUNNING}


def _summary_or_none(store: RunStore, run_id: str) -> RunSummary | None:
    try:
        return store.get_summary(run_id)
    except (ValueError, OSError):  # invalid id or unreadable: never ours to delete
        return None


def _gc_log() -> Any:
    from logging_setup import get_logger

    return get_logger(node="gc")


def _recency(run_dir: Path, summary: RunSummary | None) -> datetime:
    """When a run last finished/started; falls back to its summary sidecar's mtime.

    A directory's own mtime does not change when files inside are overwritten
    in place (forced re-runs), so it would make the newest run look oldest.
    """
    if summary is not None:
        for stamp in (summary.finished_at, summary.started_at, summary.created_at):
            if stamp is not None:
                return stamp if stamp.tzinfo else stamp.replace(tzinfo=UTC)
    sidecar = run_dir / "summary.json"
    source = sidecar if sidecar.is_file() else run_dir / "state.json"
    return datetime.fromtimestamp(_mtime(source), tz=UTC)


def recover_stale_running(store: RunStore) -> int:
    """Mark all ``PENDING``/``RUNNING`` rows ``INTERRUPTED`` (restart recovery).

    Only safe while no API worker is running against the same run store: the
    API is single-process by design (``RUN_MAX_WORKERS`` threads in one
    process), and it performs this recovery itself on startup.
    """
    return store.mark_stale_running_interrupted()


def referenced_model_versions(store: RunStore) -> set[str]:
    """Model versions referenced by runs still in the index (protected from GC)."""
    if store.index is None:
        return set()
    return {run.model_version for run in store.index.all() if run.model_version}


def _parse_int(args: list[str], flag: str) -> int | None:
    if flag in args:
        index = args.index(flag)
        if index + 1 >= len(args):
            raise ValueError(f"missing value for {flag}")
        return int(args[index + 1])
    return None


def _parse_str(args: list[str], flag: str) -> str | None:
    if flag in args:
        index = args.index(flag)
        if index + 1 >= len(args):
            raise ValueError(f"missing value for {flag}")
        return args[index + 1]
    return None


def _usage() -> str:
    return (
        "churn-survival gc [--models N] [--runs N] [--pending-ttl-days D] "
        "[--recover] [--run-dir <path>] [--model-dir <path>]"
    )


def main(argv: Iterable[str] | None = None) -> int:
    """CLI entry: ``churn-survival gc [...]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    settings = get_settings()
    try:
        models = _parse_int(args, "--models")
        runs = _parse_int(args, "--runs")
        ttl = _parse_int(args, "--pending-ttl-days")
        run_dir = _parse_str(args, "--run-dir")
        model_dir = _parse_str(args, "--model-dir")
    except ValueError as exc:
        print(f"ERROR: {exc}\nUsage: {_usage()}", file=sys.stderr)
        return 2

    max_models = models if models is not None else settings.MODEL_RETENTION_MAX
    max_runs = runs if runs is not None else settings.RUN_RETENTION_MAX
    ttl_days = ttl if ttl is not None else settings.PENDING_RUN_TTL_DAYS
    run_base = Path(run_dir) if run_dir else settings.RUN_DIR
    model_base = Path(model_dir) if model_dir else settings.MODEL_DIR

    try:
        store = RunStore(run_base)
        if "--recover" in args:
            print(
                "WARNING: --recover marks every in-flight run INTERRUPTED; run it only "
                "while the API server is stopped.",
                file=sys.stderr,
            )
        recovered = recover_stale_running(store) if "--recover" in args else 0
        pruned_runs = prune_runs(
            store, max_runs=max_runs, pending_ttl_days=ttl_days
        )
        pruned_models = prune_model_artifacts(
            model_base, max_models, protected=referenced_model_versions(store)
        )
        pruned_drafts = prune_mapping_drafts(
            Path(settings.CONFIG_DIR) / "mappings" / "drafts", ttl_days
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary must fail loudly
        print(f"ERROR: gc failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"GC: recovered={recovered} runs_pruned={len(pruned_runs)} "
        f"models_pruned={len(pruned_models)} drafts_pruned={len(pruned_drafts)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

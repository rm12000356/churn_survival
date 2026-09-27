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

from config.settings import get_settings
from orchestration.persistence import RunStore
from schemas.run import RunExecutionStatus

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


def prune_model_artifacts(model_dir: str | Path, max_count: int) -> list[Path]:
    """Keep the newest ``max_count`` model artifact directories (by mtime)."""
    base = Path(model_dir)
    if max_count <= 0 or not base.is_dir():
        return []
    candidates = [
        child
        for child in base.iterdir()
        if child.is_dir() and (child / "model.json").is_file()
    ]
    candidates.sort(key=_mtime, reverse=True)
    deleted: list[Path] = []
    for stale in candidates[max_count:]:
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
    """TTL-prune pending/terminal-unproductive runs, then count-prune the rest."""
    deleted: list[str] = []

    if pending_ttl_days > 0 and store.base_dir.is_dir():
        cutoff = (now or _utcnow()) - timedelta(days=pending_ttl_days)
        for child in sorted(store.base_dir.iterdir()):
            if not child.is_dir() or not (child / "state.json").is_file():
                continue
            summary = store.get_summary(child.name)
            if summary is None or summary.execution_status not in _TTL_STATUSES:
                continue
            if datetime.fromtimestamp(_mtime(child), tz=UTC) < cutoff:
                store.delete(child.name)
                deleted.append(child.name)

    if max_runs > 0 and store.base_dir.is_dir():
        remaining = [
            child
            for child in store.base_dir.iterdir()
            if child.is_dir() and (child / "state.json").is_file()
        ]
        remaining.sort(key=_mtime, reverse=True)
        for stale in remaining[max_runs:]:
            store.delete(stale.name)
            deleted.append(stale.name)

    return deleted


def recover_stale_running(store: RunStore) -> int:
    """Mark all ``RUNNING`` rows ``INTERRUPTED`` (process restart recovery)."""
    return store.mark_stale_running_interrupted()


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
        recovered = recover_stale_running(store) if "--recover" in args else 0
        pruned_runs = prune_runs(
            store, max_runs=max_runs, pending_ttl_days=ttl_days
        )
        pruned_models = prune_model_artifacts(model_base, max_models)
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

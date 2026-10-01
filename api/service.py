"""Run triggering / preparation (ROADMAP Phase 8, Task 8.2).

The trigger endpoint's job: **prepare** a run identity (decision-free routing +
content-addressed id, no node execution) and, when the status matrix says so,
**enqueue** one background execution. Execution itself calls ``run_pipeline``
once and persists the result — the API never re-derives decisions on read.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from config.loader import load_action_rules, load_node1_config
from config.models import ActionRulesConfig, Node1Config
from config.settings import Settings
from logging_setup import get_logger
from orchestration.identity import CODE_SEMANTICS_VERSION, llm_identity
from orchestration.persistence import RunStore, compute_trigger_run_id
from orchestration.routing import build_adapters, resolve_node1_version
from schemas.run import (
    RoutingIdentity,
    RunCreatedResponse,
    RunExecutionStatus,
    RunLinks,
)

__all__ = [
    "PreparedRun",
    "RunSpec",
    "client_error_text",
    "execute_run",
    "llm_client_or_none",
    "prepare_run",
    "run_links",
]

_LINK_PREFIX = "/runs"


def llm_client_or_none(settings: Settings) -> Any | None:
    """Build the optional LLM client from settings (``None`` when disabled).

    The LLM is explanation-polish only in Node 5 and thread extraction in Node 3;
    it never decides a level/score/rank. When ``LLM_PROVIDER=none`` every run is
    deterministically template-only.
    """
    if settings.LLM_PROVIDER == "none":
        return None
    from router.llm_mapper import create_llm_client

    return create_llm_client()


def run_links(run_id: str) -> RunLinks:
    """Hypermedia links for a run (served by the read endpoints)."""
    base = f"{_LINK_PREFIX}/{run_id}"
    return RunLinks(
        detail=base,
        report=f"{base}/report",
        report_html=f"{base}/report.html",
        ranked_accounts=f"{base}/ranked-accounts",
    )


def created_response(
    run_id: str, execution_status: RunExecutionStatus
) -> RunCreatedResponse:
    return RunCreatedResponse(
        run_id=run_id, execution_status=execution_status, links=run_links(run_id)
    )


@dataclass(frozen=True)
class RunSpec:
    """Normalized, validated run request."""

    raw_path: str
    node1_version: str
    node2_version: str
    node3_version: str
    node4_version: str
    node5_version: str
    action_rules_version: str
    reference_date: date
    support_data: list[dict[str, Any]] | None
    persist_artifact: bool
    # Nodes that may use the LLM for this run ("node3", "node5"); empty = none.
    llm_nodes: frozenset[str] = frozenset()


@dataclass(frozen=True)
class PreparedRun:
    """Everything the trigger needs to dedup and the worker needs to execute."""

    run_id: str
    routing: RoutingIdentity
    node1_config: Node1Config
    node1_version: str
    node1_warning: str | None
    action_rules: ActionRulesConfig


def prepare_run(settings: Settings, spec: RunSpec) -> PreparedRun:
    """Routing-inclusive run identity, computed **without** executing a node."""
    adapters = build_adapters(settings.CONFIG_DIR)
    # Resolve ``"auto"`` to the deployment Node 1 config from the matched confirmed
    # mapping, so the run identity matches what the worker will actually execute.
    node1_version, node1_warning = resolve_node1_version(
        spec.raw_path,
        spec.node1_version,
        adapters=adapters,
        config_dir=settings.CONFIG_DIR,
    )
    node1_config = load_node1_config(node1_version)
    action_rules = load_action_rules(spec.action_rules_version)
    config_versions = {
        "node1": node1_version,
        "node2": spec.node2_version,
        "node3": spec.node3_version,
        "node4": spec.node4_version,
        "node5": spec.node5_version,
        "action_rules": action_rules.action_rules_version,
    }
    # Must match run_pipeline's identity inputs (orchestration.graph._select_llm_nodes).
    llm = llm_identity(llm_client_or_none(settings)) if spec.llm_nodes else None
    if llm is not None:
        config_versions["llm"] = llm
        config_versions["llm_nodes"] = ",".join(sorted(spec.llm_nodes))
    config_versions["semantics"] = CODE_SEMANTICS_VERSION
    run_id, routing = compute_trigger_run_id(
        spec.raw_path,
        node1_config=node1_config,
        adapters=adapters,
        config_versions=config_versions,
        reference_date=spec.reference_date,
        support_data=spec.support_data,
    )
    return PreparedRun(
        run_id=run_id,
        routing=routing,
        node1_config=node1_config,
        node1_version=node1_version,
        node1_warning=node1_warning,
        action_rules=action_rules,
    )


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _merge(store: RunStore, run_id: str, **changes: Any) -> None:
    """Update index columns in one statement (no read-modify-write race)."""
    if store.index is None:
        return
    store.index.update_fields(run_id, **changes)


#: This project's top-level packages (pyproject ``[tool.hatch...] packages``).
_PROJECT_PACKAGES = frozenset(
    {
        "adapters",
        "schemas",
        "router",
        "node1",
        "node2",
        "node3",
        "node4",
        "node5",
        "orchestration",
        "api",
        "config",
        "pipeline",
    }
)


def _raised_by_this_codebase(exc: BaseException) -> bool:
    """True when the innermost frame belongs to one of this project's packages.

    Decided by the frame's *module name*, not its file path, so a virtualenv
    or a site-packages copy inside the repo can never be mistaken for project
    code (REVIEW LOW).
    """
    tb = exc.__traceback__
    if tb is None:
        return False
    while tb.tb_next is not None:
        tb = tb.tb_next
    module = str(tb.tb_frame.f_globals.get("__name__", ""))
    return module.split(".", 1)[0] in _PROJECT_PACKAGES


def client_error_text(exc: BaseException) -> str:
    """User-facing error text that never leaks internals (paths, input values).

    Validation errors written by this codebase (bad mapping, bad config version)
    keep their message; anything from a library (pandas/pydantic internals,
    provider/HTTP errors) is reduced to its type and logged server-side.
    """
    if isinstance(exc, FileNotFoundError):
        return "a referenced file or config version does not exist"
    if isinstance(exc, ValueError) and _raised_by_this_codebase(exc):
        return str(exc)
    get_logger(node="api").warning(
        "client_error", error=type(exc).__name__, detail=_redacted(str(exc))
    )
    return f"{type(exc).__name__} (details are in the server log)"


def _redacted(text: str, limit: int = 300) -> str:
    """Log-safe text: configured secrets replaced, length bounded."""
    from node3.sources.errors import redact_secrets

    try:
        from config.settings import get_settings

        settings = get_settings()
        secrets = (
            settings.API_KEY,
            settings.LLM_API_KEY,
            settings.X_CLIENT_SECRET,
            settings.X_ACCESS_TOKEN,
            settings.GMAIL_CLIENT_SECRET,
            settings.GMAIL_REFRESH_TOKEN,
        )
    except Exception:  # noqa: BLE001 - settings unavailable: still truncate
        secrets = ()
    return redact_secrets(text, secrets)[:limit]


class _PersistError(RuntimeError):
    """``store.save`` failed after the pipeline itself finished."""


def _failure_errors(code: str, exc: BaseException) -> list[dict[str, Any]]:
    """The structured, client-safe error stored on a FAILED run (REVIEW N-M2)."""
    return [{"code": code, "stage": "api", "message": client_error_text(exc)}]


def execute_run(
    store: RunStore,
    settings: Settings,
    *,
    run_id: str,
    spec: RunSpec,
    prepared: PreparedRun,
) -> None:
    """Run the pipeline once and persist it. Never raises (structured FAILED)."""
    log = get_logger(node="api")
    log.info("run_started", run_id=run_id, raw_file=Path(spec.raw_path).name)

    # Queued runs are PENDING; the row only turns RUNNING when work starts.
    _merge(
        store,
        run_id,
        execution_status=RunExecutionStatus.RUNNING,
        started_at=_utcnow(),
    )
    try:
        _execute_and_persist(store, settings, run_id=run_id, spec=spec, prepared=prepared)
    except _PersistError as exc:
        # The pipeline finished but its result could not be stored: report that,
        # not a pipeline failure.
        cause = exc.__cause__ or exc
        _merge(
            store,
            run_id,
            execution_status=RunExecutionStatus.FAILED,
            error_code="PERSIST_ERROR",
            errors=_failure_errors("PERSIST_ERROR", cause),
            finished_at=_utcnow(),
        )
        log.error(
            "run_persist_failed", run_id=run_id, error=type(cause).__name__, exc_info=cause
        )
    except Exception as exc:  # noqa: BLE001 - the worker must never leave RUNNING rows
        _merge(
            store,
            run_id,
            execution_status=RunExecutionStatus.FAILED,
            error_code="WORKER_ERROR",
            errors=_failure_errors("WORKER_ERROR", exc),
            finished_at=_utcnow(),
        )
        log.error("run_worker_failed", run_id=run_id, error=type(exc).__name__, exc_info=exc)


def _execute_and_persist(
    store: RunStore,
    settings: Settings,
    *,
    run_id: str,
    spec: RunSpec,
    prepared: PreparedRun,
) -> None:
    from orchestration.graph import run_pipeline

    log = get_logger(node="api")

    def _report_stage(stage: Any) -> None:
        """Mirror the pipeline's current stage into the index the UI polls.

        Written live so a long run (e.g. Node 3 extraction) shows real progress
        instead of appearing stuck before Node 1; ``store.save`` overwrites it
        with the terminal stage when the run finishes.
        """
        _merge(store, run_id, stage=getattr(stage, "value", str(stage)))

    try:
        result = run_pipeline(
            Path(spec.raw_path),
            node1_version=prepared.node1_version,
            node2_version=spec.node2_version,
            node3_version=spec.node3_version,
            node4_version=spec.node4_version,
            node5_version=spec.node5_version,
            support_data=spec.support_data,
            action_rules=prepared.action_rules,
            llm_client=llm_client_or_none(settings) if spec.llm_nodes else None,
            llm_nodes=spec.llm_nodes,
            settings=settings,
            config_dir=settings.CONFIG_DIR,
            persist_artifact=spec.persist_artifact,
            reference_date=spec.reference_date,
            on_stage=_report_stage,
        )
    except Exception as exc:  # noqa: BLE001 - the worker must record a terminal state
        _merge(
            store,
            run_id,
            execution_status=RunExecutionStatus.FAILED,
            error_code=type(exc).__name__,
            errors=_failure_errors(type(exc).__name__, exc),
            finished_at=_utcnow(),
        )
        log.error("run_failed", run_id=run_id, error_code=type(exc).__name__, exc_info=exc)
        return

    actual_id = result.state.run_id
    if not actual_id:
        # Stopped before an identity existed (e.g. routing failed).
        error_code = result.state.errors[0].get("code") if result.state.errors else "NO_RUN_ID"
        _merge(
            store,
            run_id,
            execution_status=RunExecutionStatus.FAILED,
            error_code=error_code,
            finished_at=_utcnow(),
        )
        log.error("run_failed", run_id=run_id, error_code=error_code)
        return

    if prepared.node1_warning:
        result.state.warnings.append(prepared.node1_warning)
    try:
        store.save(result)
    except Exception as first:  # noqa: BLE001 - one retry for a transient I/O error
        log.warning("run_persist_retry", run_id=run_id, error=type(first).__name__)
        try:
            store.save(result)
        except Exception as exc:
            raise _PersistError("could not persist the run result") from exc
    if actual_id != run_id and store.index is not None:
        # The pipeline resolved a different identity than the trigger predicted;
        # move the placeholder's bookkeeping and lineage to the real run.
        placeholder = store.index.get(run_id)
        store.index.delete(run_id)
        if placeholder is not None:
            _merge(
                store,
                actual_id,
                created_at=placeholder.created_at,
                started_at=placeholder.started_at,
            )
            for row in store.index.all():
                if row.superseded_by == run_id:
                    _merge(store, row.run_id, superseded_by=actual_id)
    _merge(store, actual_id, finished_at=_utcnow())
    log.info(
        "run_completed",
        run_id=actual_id,
        pipeline_status=result.state.status.value if result.state.status else None,
    )

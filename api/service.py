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
    if store.index is None:
        return
    summary = store.get_summary(run_id)
    if summary is not None:
        store.index.upsert(summary.model_copy(update=changes))


def execute_run(
    store: RunStore,
    settings: Settings,
    *,
    run_id: str,
    spec: RunSpec,
    prepared: PreparedRun,
) -> None:
    """Run the pipeline once and persist it. Never raises (structured FAILED)."""
    from orchestration.graph import run_pipeline

    log = get_logger(node="api")
    log.info("run_enqueued", run_id=run_id, raw_file=Path(spec.raw_path).name)

    _merge(
        store,
        run_id,
        execution_status=RunExecutionStatus.RUNNING,
        started_at=_utcnow(),
    )
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
            llm_client=llm_client_or_none(settings),
            settings=settings,
            config_dir=settings.CONFIG_DIR,
            persist_artifact=spec.persist_artifact,
            reference_date=spec.reference_date,
        )
    except Exception as exc:  # noqa: BLE001 - the worker must record a terminal state
        _merge(
            store,
            run_id,
            execution_status=RunExecutionStatus.FAILED,
            error_code=type(exc).__name__,
            finished_at=_utcnow(),
        )
        log.error("run_failed", run_id=run_id, error_code=type(exc).__name__)
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
    store.save(result)
    if actual_id != run_id and store.index is not None:
        store.index.delete(run_id)
    _merge(store, actual_id, finished_at=_utcnow())
    log.info(
        "run_completed",
        run_id=actual_id,
        pipeline_status=result.state.status.value if result.state.status else None,
    )

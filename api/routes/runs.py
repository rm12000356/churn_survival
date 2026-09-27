"""Run endpoints (ROADMAP Phase 8, Task 8.2).

**Read endpoints never compute.** They deserialize stored bytes only — no
``run_pipeline``, no node function, no re-derivation of a level/score/rank.

**The single mutating endpoint** is ``POST /runs``: it prepares the run identity
(decision-free) and enqueues exactly one background execution. The status matrix
(D-P4/D-P11) decides whether to enqueue, return a cached result, or error.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse

from api.deps import get_store, require_auth, require_writes, resolve_raw_path
from api.schemas import RunTriggerRequest
from api.service import PreparedRun, RunSpec, created_response, execute_run, prepare_run
from config.settings import Settings
from orchestration.persistence import RunStore
from schemas.node2 import Node2Output
from schemas.node3 import Node3Output
from schemas.node4 import Node4Output
from schemas.node5 import Node5Output
from schemas.run import (
    RoutingIdentitySource,
    RunExecutionStatus,
    RunListResponse,
    RunSummary,
)
from schemas.validation import Node1Output

router = APIRouter(tags=["runs"])

_NODE_MODELS: dict[str, type[Any]] = {
    "node1": Node1Output,
    "node2": Node2Output,
    "node3": Node3Output,
    "node4": Node4Output,
}


def _load_node(store: RunStore, run_id: str, node: str) -> Any:
    payload = store.read_node_output(run_id, node)
    if payload is None:
        if store.get_summary(run_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
        raise HTTPException(
            status_code=404, detail=f"run {run_id!r} has no {node} output"
        )
    return _NODE_MODELS[node].model_validate(payload)


@router.get("/runs", response_model=RunListResponse)
def list_runs(
    store: Annotated[RunStore, Depends(get_store)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    status_filter: Annotated[RunExecutionStatus | None, Query(alias="status")] = None,
    model_version: str | None = None,
) -> RunListResponse:
    runs = store.list_runs(limit=limit, status=status_filter, model_version=model_version)
    return RunListResponse(runs=runs, total=len(runs))


@router.get("/runs/{run_id}", response_model=RunSummary)
def get_run(
    run_id: str, store: Annotated[RunStore, Depends(get_store)]
) -> RunSummary:
    summary = store.get_summary(run_id)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
    return summary


@router.get("/runs/{run_id}/report", response_model=Node5Output)
def get_report(
    run_id: str, store: Annotated[RunStore, Depends(get_store)]
) -> Node5Output:
    payload = store.read_node_output(run_id, "node5")
    if payload is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} has no report")
    return Node5Output.model_validate(payload)


@router.get("/runs/{run_id}/report.html", response_class=HTMLResponse)
def get_report_html(run_id: str, store: Annotated[RunStore, Depends(get_store)]) -> Response:
    if store.get_summary(run_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
    html = store.read_report_html(run_id)
    if html is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} has no report")
    return HTMLResponse(content=html)


@router.get("/runs/{run_id}/ranked-accounts", response_model=Node4Output)
def get_ranked_accounts(
    run_id: str, store: Annotated[RunStore, Depends(get_store)]
) -> Node4Output:
    return _load_node(store, run_id, "node4")


@router.get("/runs/{run_id}/node1", response_model=Node1Output)
def get_node1(run_id: str, store: Annotated[RunStore, Depends(get_store)]) -> Any:
    return _load_node(store, run_id, "node1")


@router.get("/runs/{run_id}/node2", response_model=Node2Output)
def get_node2(run_id: str, store: Annotated[RunStore, Depends(get_store)]) -> Any:
    return _load_node(store, run_id, "node2")


@router.get("/runs/{run_id}/node3", response_model=Node3Output)
def get_node3(run_id: str, store: Annotated[RunStore, Depends(get_store)]) -> Any:
    return _load_node(store, run_id, "node3")


@router.get("/runs/{run_id}/node4", response_model=Node4Output)
def get_node4(run_id: str, store: Annotated[RunStore, Depends(get_store)]) -> Any:
    return _load_node(store, run_id, "node4")


def _enqueue(
    request: Request,
    store: RunStore,
    settings: Settings,
    spec: RunSpec,
    prepared: PreparedRun,
    *,
    supersedes_run_id: str | None,
) -> JSONResponse:
    now = datetime.now(UTC)
    existing = store.get_summary(prepared.run_id)
    if existing is None:
        row = RunSummary(
            run_id=prepared.run_id,
            execution_status=RunExecutionStatus.RUNNING,
            raw_path=spec.raw_path,
            reference_date=spec.reference_date,
            routing_identity_source=RoutingIdentitySource.COMPUTED,
            routing_adapter=prepared.routing.adapter,
            routing_adapter_version=prepared.routing.adapter_version,
            routing_confidence=prepared.routing.confidence,
            created_at=now,
            started_at=now,
        )
    else:
        row = existing.model_copy(
            update={"execution_status": RunExecutionStatus.RUNNING, "started_at": now}
        )
    if store.index is not None:
        store.index.upsert(row)
        if supersedes_run_id:
            previous = store.get_summary(supersedes_run_id)
            if previous is not None:
                store.index.upsert(
                    previous.model_copy(update={"superseded_by": prepared.run_id})
                )

    request.app.state.executor.submit(
        execute_run, store, settings, run_id=prepared.run_id, spec=spec, prepared=prepared
    )
    return JSONResponse(
        content=created_response(
            prepared.run_id, RunExecutionStatus.RUNNING
        ).model_dump(mode="json"),
        status_code=status.HTTP_202_ACCEPTED,
    )


@router.post("/runs", response_model=None)
def trigger_run(
    body: RunTriggerRequest,
    request: Request,
    store: Annotated[RunStore, Depends(get_store)],
    _writes: Annotated[None, Depends(require_writes)],
    _actor: Annotated[str, Depends(require_auth)],
    force: bool = False,
) -> Response:
    settings: Settings = request.app.state.settings
    raw_path = resolve_raw_path(settings, body.raw_path)
    spec = RunSpec(
        raw_path=str(raw_path),
        node1_version=body.node1_version,
        node2_version=body.node2_version,
        node3_version=body.node3_version,
        node4_version=body.node4_version,
        node5_version=body.node5_version,
        action_rules_version=body.action_rules_version,
        reference_date=body.reference_date or settings.REFERENCE_DATE,
        support_data=body.support_data,
        persist_artifact=body.persist_artifact,
    )
    try:
        prepared = prepare_run(settings, spec)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - malformed identity inputs
        raise HTTPException(
            status_code=422,
            detail=f"could not prepare run: {exc}",
        ) from exc

    existing = store.get_summary(prepared.run_id)
    if existing is None:
        return _enqueue(
            request, store, settings, spec, prepared, supersedes_run_id=body.supersedes_run_id
        )

    current = existing.execution_status
    if current in (RunExecutionStatus.PENDING, RunExecutionStatus.RUNNING):
        if force:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="run is already in progress; cannot force",
            )
        return JSONResponse(
            content=created_response(prepared.run_id, current).model_dump(mode="json"),
            status_code=status.HTTP_202_ACCEPTED,
        )

    if current is RunExecutionStatus.COMPLETED and not force:
        return JSONResponse(
            content=existing.model_dump(mode="json"),
            status_code=status.HTTP_200_OK,
        )

    if current in (RunExecutionStatus.STOPPED_NEEDS_MAPPING, RunExecutionStatus.STOPPED_VALIDATION):
        if force:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "stopped runs cannot be forced; change the input/mapping so the "
                    "run identity changes, then trigger again"
                ),
            )
        return JSONResponse(
            content=existing.model_dump(mode="json"),
            status_code=status.HTTP_200_OK,
        )

    # COMPLETED+force, FAILED, INTERRUPTED -> resubmit in place.
    return _enqueue(
        request, store, settings, spec, prepared, supersedes_run_id=body.supersedes_run_id
    )

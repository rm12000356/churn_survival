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
from pydantic import ValidationError

from api.deps import (
    client_key,
    get_store,
    require_auth,
    require_writes,
    resolve_raw_path,
    safe_id,
)
from api.schemas import RunTriggerRequest
from api.service import (
    PreparedRun,
    RunSpec,
    client_error_text,
    created_response,
    execute_run,
    prepare_run,
)
from config.settings import Settings
from logging_setup import get_logger
from orchestration.persistence import RunStore
from schemas.node2 import Node2Output
from schemas.node3 import Node3Output
from schemas.node4 import Node4Output
from schemas.node5 import Node5Output
from schemas.run import (
    RoutingIdentitySource,
    RunCreatedResponse,
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

# The stored report is rendered from customer data and (validated) LLM text:
# serve it sandboxed so no script in it can run or reach the app's origin.
_REPORT_HEADERS = {
    "Content-Security-Policy": (
        "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:"
    ),
    "X-Content-Type-Options": "nosniff",
}


def _run_id(run_id: str) -> str:
    return safe_id(run_id, kind="run")


def _validated(model: type[Any], payload: Any, run_id: str, node: str) -> Any:
    """Validate a stored output; a corrupt one is a structured 500, not a crash."""
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        get_logger(node="api").error(
            "stored_output_invalid", run_id=run_id, node=node, n_errors=exc.error_count()
        )
        raise HTTPException(
            status_code=500,
            detail=f"the stored {node} output of run {run_id!r} is unreadable",
        ) from exc


def _load_node(store: RunStore, run_id: str, node: str) -> Any:
    payload = store.read_node_output(_run_id(run_id), node)
    if payload is None:
        if store.get_summary(run_id) is None:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
        raise HTTPException(
            status_code=404, detail=f"run {run_id!r} has no {node} output"
        )
    return _validated(_NODE_MODELS[node], payload, run_id, node)


@router.get("/runs", response_model=RunListResponse)
def list_runs(
    store: Annotated[RunStore, Depends(get_store)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[RunExecutionStatus | None, Query(alias="status")] = None,
    model_version: str | None = None,
) -> RunListResponse:
    """Newest first; page with ``offset`` (REVIEW N-M16)."""
    runs = store.list_runs(
        limit=limit, offset=offset, status=status_filter, model_version=model_version
    )
    if offset == 0 and len(runs) < limit:
        # The whole result fits in this page: count what was actually returned,
        # so an undecodable index row cannot inflate the total.
        total = len(runs)
    else:
        counted = store.count_runs(status=status_filter, model_version=model_version)
        total = max(counted, offset + len(runs))
    return RunListResponse(runs=runs, total=total)


@router.get("/runs/{run_id}", response_model=RunSummary)
def get_run(
    run_id: str, store: Annotated[RunStore, Depends(get_store)]
) -> RunSummary:
    summary = store.get_summary(_run_id(run_id))
    if summary is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
    return summary


@router.get("/runs/{run_id}/report", response_model=Node5Output)
def get_report(
    run_id: str, store: Annotated[RunStore, Depends(get_store)]
) -> Node5Output:
    payload = store.read_node_output(_run_id(run_id), "node5")
    if payload is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} has no report")
    report: Node5Output = _validated(Node5Output, payload, run_id, "node5")
    return report


@router.get("/runs/{run_id}/report.html", response_class=HTMLResponse)
def get_report_html(run_id: str, store: Annotated[RunStore, Depends(get_store)]) -> Response:
    if store.get_summary(_run_id(run_id)) is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id!r}")
    html = store.read_report_html(run_id)
    if html is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} has no report")
    return HTMLResponse(content=html, headers=_REPORT_HEADERS)


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
    slots = request.app.state.run_slots
    if not slots.try_acquire():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"{slots.capacity} runs are already queued or running; "
                "try again when one finishes"
            ),
            headers={"Retry-After": "30"},
        )
    try:
        return _claim_and_submit(
            request, store, settings, spec, prepared, supersedes_run_id=supersedes_run_id
        )
    except BaseException:
        slots.release()
        raise


def _claim_and_submit(
    request: Request,
    store: RunStore,
    settings: Settings,
    spec: RunSpec,
    prepared: PreparedRun,
    *,
    supersedes_run_id: str | None,
) -> JSONResponse:
    """Claim the row and hand the run to the executor; the caller holds a slot.

    The slot is released here once nothing is queued (duplicate claim, inline
    executor) or when the queued run finishes; on an exception the caller does.
    """
    slots = request.app.state.run_slots
    now = datetime.now(UTC)
    row = RunSummary(
        run_id=prepared.run_id,
        execution_status=RunExecutionStatus.PENDING,
        raw_path=spec.raw_path,
        reference_date=spec.reference_date,
        routing_identity_source=RoutingIdentitySource.COMPUTED,
        routing_adapter=prepared.routing.adapter,
        routing_adapter_version=prepared.routing.adapter_version,
        routing_confidence=prepared.routing.confidence,
        created_at=now,
        started_at=None,
    )
    if store.index is not None:
        # Atomic claim: when two identical triggers race, exactly one enqueues.
        if not store.index.claim(row):
            slots.release()  # nothing was queued
            current = store.get_summary(prepared.run_id)
            in_flight = current.execution_status if current else RunExecutionStatus.PENDING
            return JSONResponse(
                content=created_response(prepared.run_id, in_flight).model_dump(mode="json"),
                status_code=status.HTTP_202_ACCEPTED,
            )
        if supersedes_run_id and supersedes_run_id != prepared.run_id:
            store.index.update_fields(supersedes_run_id, superseded_by=prepared.run_id)

    try:
        future = request.app.state.executor.submit(
            execute_run, store, settings, run_id=prepared.run_id, spec=spec, prepared=prepared
        )
    except Exception as exc:  # noqa: BLE001 - e.g. executor shut down / RuntimeError
        # The row was claimed: never leave it PENDING, or every retry would be
        # treated as in flight and `force` would answer 409 (REVIEW N-M3).
        if store.index is not None:
            store.index.update_fields(
                prepared.run_id,
                execution_status=RunExecutionStatus.FAILED,
                error_code="ENQUEUE_FAILED",
                finished_at=datetime.now(UTC),
            )
        get_logger(node="api").error(
            "run_enqueue_failed", run_id=prepared.run_id, error=type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the run could not be queued; try again",
        ) from exc
    if hasattr(future, "add_done_callback"):  # injected test executors may run inline
        future.add_done_callback(_log_worker_crash)
        future.add_done_callback(slots.release)
    else:
        slots.release()  # ran inline: already finished
    return JSONResponse(
        content=created_response(
            prepared.run_id, RunExecutionStatus.PENDING
        ).model_dump(mode="json"),
        status_code=status.HTTP_202_ACCEPTED,
    )


def _log_worker_crash(future: Any) -> None:
    """``execute_run`` never raises by design; log it loudly if it ever does."""
    if future.cancelled():
        return
    exc = future.exception()
    if exc is not None:
        get_logger(node="api").error("run_worker_crashed", error=type(exc).__name__)


@router.post(
    "/runs",
    response_model=None,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        200: {"model": RunSummary, "description": "Cached terminal run (no new work)."},
        202: {"model": RunCreatedResponse, "description": "Run queued or already running."},
        400: {"description": "force on a stopped run, or raw_path outside RAW_DATA_DIR."},
        401: {"description": "Invalid or missing API key."},
        403: {"description": "Writes are disabled."},
        409: {"description": "force while the run is in progress."},
        422: {"description": "The run could not be prepared."},
        429: {"description": "Too many triggers, or too many queued runs."},
        503: {"description": "The run could not be queued."},
    },
)
def trigger_run(
    body: RunTriggerRequest,
    request: Request,
    store: Annotated[RunStore, Depends(get_store)],
    _writes: Annotated[None, Depends(require_writes)],
    _actor: Annotated[str, Depends(require_auth)],
    force: bool = False,
) -> Response:
    settings: Settings = request.app.state.settings
    request.app.state.run_trigger_limit.acquire(client_key(request))
    raw_path = resolve_raw_path(settings, body.raw_path)
    if body.supersedes_run_id is not None and store.get_summary(body.supersedes_run_id) is None:
        raise HTTPException(status_code=422, detail="supersedes_run_id is not a known run")
    llm_nodes = frozenset(
        node for node, wanted in (("node3", body.llm_node3), ("node5", body.llm_node5)) if wanted
    )
    if llm_nodes and settings.LLM_PROVIDER == "none":
        raise HTTPException(
            status_code=422,
            detail="LLM use was requested but no LLM is configured (LLM_PROVIDER=none)",
        )
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
        llm_nodes=llm_nodes,
    )
    try:
        prepared = prepare_run(settings, spec)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - malformed identity inputs
        raise HTTPException(
            status_code=422,
            detail=f"could not prepare run: {client_error_text(exc)}",
        ) from exc

    if body.supersedes_run_id == prepared.run_id:
        raise HTTPException(status_code=422, detail="a run cannot supersede itself")

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

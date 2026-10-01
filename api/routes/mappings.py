"""Mapping draft/confirm endpoints (ROADMAP Phase 8, Task 8.2).

``POST /mappings/confirm`` is the only way a mapping becomes configuration, and
it is always write-gated + authenticated. It routes the human-approved report
through :func:`orchestration.mapping.persist_confirmed_mapping` (the gate) and
never calls ``confirm_and_persist`` directly.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import (
    clean_label,
    get_app_settings,
    require_auth,
    require_writes,
    resolve_raw_path,
)
from api.limits import RateLimit
from api.schemas import (
    MappingConfirmRequest,
    MappingConfirmResponse,
    MappingDraftRequest,
)
from api.service import client_error_text, llm_client_or_none
from config.settings import Settings
from orchestration.mapping import CallbackMappingGate, persist_confirmed_mapping
from orchestration.routing import fingerprint_file
from router.llm_mapper import MappingAlreadyConfirmedError
from schemas.mapping import MappingReport, SourceFingerprint

router = APIRouter(tags=["mappings"])


#: Paid LLM calls: a process-wide budget, whoever asks.
_LLM_DRAFT_LIMIT = RateLimit(per_minute=10, what="LLM mapping drafts")


def _missing_source_columns(report: MappingReport, fingerprint: SourceFingerprint) -> list[str]:
    """Source columns the mapping reads that the dataset does not have."""
    available = set(fingerprint.column_names)
    wanted = [
        mapping.source_column
        for mapping in report.proposed_mappings
        if (mapping.transformation or "").strip() != "row_number"
    ]
    wanted.extend(extra.source for extra in report.suggested_extra_features)
    return sorted({column for column in wanted if column not in available})


@router.post("/mappings/draft", response_model=MappingReport)
def draft_mapping(
    body: MappingDraftRequest,
    settings: Annotated[Settings, Depends(get_app_settings)],
    _writes: Annotated[None, Depends(require_writes)],
    _actor: Annotated[str, Depends(require_auth)],
) -> MappingReport:
    """Draft a mapping report (deterministic skeleton, or LLM proposal)."""
    path = resolve_raw_path(settings, body.raw_path)
    from node1.node import build_draft_mapping_report, run_mapping_workflow

    try:
        if body.use_llm:
            _LLM_DRAFT_LIMIT.acquire()  # paid calls: bounded per minute
            client = llm_client_or_none(settings)
            return run_mapping_workflow(path, client=client)  # type: ignore[no-any-return]
        return build_draft_mapping_report(path)  # type: ignore[no-any-return]
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - surface as a structured 422
        raise HTTPException(
            status_code=422, detail=f"mapping draft failed: {client_error_text(exc)}"
        ) from exc


@router.post("/mappings/confirm", response_model=MappingConfirmResponse)
def confirm_mapping(
    body: MappingConfirmRequest,
    settings: Annotated[Settings, Depends(get_app_settings)],
    _writes: Annotated[None, Depends(require_writes)],
    actor: Annotated[str, Depends(require_auth)],
) -> MappingConfirmResponse:
    """Persist a human-approved mapping through the confirmation gate (D-O3).

    The report is bound to the dataset it is confirmed for: its
    ``source_fingerprint`` becomes that dataset's fingerprint, so a mapping file
    written elsewhere (or edited by hand) routes this dataset from now on.
    """
    fingerprint: SourceFingerprint | None = body.fingerprint
    if fingerprint is None:
        if body.raw_path is None:
            raise HTTPException(
                status_code=422,
                detail="provide either fingerprint or raw_path to confirm a mapping",
            )
        path = resolve_raw_path(settings, body.raw_path)
        fingerprint = fingerprint_file(path)

    report = body.report.model_copy(update={"source_fingerprint": fingerprint})
    missing = _missing_source_columns(report, fingerprint)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=(
                "mapping references columns that are not in this dataset: "
                + ", ".join(missing)
            ),
        )

    # The body name is a claim; always record the authenticated key next to it.
    claimed = clean_label(body.confirmed_by)
    confirmed_by = f"{claimed} [{actor}]" if claimed else actor
    gate = CallbackMappingGate(lambda approved_report, _fp: approved_report, name=confirmed_by)
    approved = gate.confirm(report, fingerprint)
    if approved is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="mapping declined")
    try:
        config = persist_confirmed_mapping(
            approved,
            config_dir=settings.CONFIG_DIR,
            confirmed_by=confirmed_by,
            node1_config_version=body.node1_config_version,
        )
    except MappingAlreadyConfirmedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - rejected mapping is a client error
        raise HTTPException(
            status_code=422,
            detail=f"mapping confirmation rejected: {client_error_text(exc)}",
        ) from exc

    return MappingConfirmResponse(
        mapping_version=config.mapping_version, fingerprint=fingerprint
    )

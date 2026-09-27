"""Mapping draft/confirm endpoints (ROADMAP Phase 8, Task 8.2).

``POST /mappings/confirm`` is the only way a mapping becomes configuration, and
it is always write-gated + authenticated. It routes the human-approved report
through :func:`orchestration.mapping.persist_confirmed_mapping` (the gate) and
never calls ``confirm_and_persist`` directly.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import get_app_settings, require_auth, require_writes, resolve_raw_path
from api.schemas import (
    MappingConfirmRequest,
    MappingConfirmResponse,
    MappingDraftRequest,
)
from api.service import llm_client_or_none
from config.settings import Settings
from orchestration.mapping import CallbackMappingGate, persist_confirmed_mapping
from router.fingerprint import extract_fingerprint
from schemas.mapping import MappingReport, SourceFingerprint

router = APIRouter(tags=["mappings"])


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
            client = llm_client_or_none(settings)
            return run_mapping_workflow(path, client=client)  # type: ignore[no-any-return]
        return build_draft_mapping_report(path)  # type: ignore[no-any-return]
    except Exception as exc:  # noqa: BLE001 - surface as a structured 422
        raise HTTPException(
            status_code=422,
            detail=f"mapping draft failed: {exc}",
        ) from exc


@router.post("/mappings/confirm", response_model=MappingConfirmResponse)
def confirm_mapping(
    body: MappingConfirmRequest,
    settings: Annotated[Settings, Depends(get_app_settings)],
    _writes: Annotated[None, Depends(require_writes)],
    actor: Annotated[str, Depends(require_auth)],
) -> MappingConfirmResponse:
    """Persist a human-approved mapping through the confirmation gate (D-O3)."""
    fingerprint: SourceFingerprint | None = body.fingerprint
    if fingerprint is None:
        if body.raw_path is None:
            raise HTTPException(
                status_code=422,
                detail="provide either fingerprint or raw_path to confirm a mapping",
            )
        path = resolve_raw_path(settings, body.raw_path)
        from node1.node import load_raw

        fingerprint = extract_fingerprint(load_raw(path))

    confirmed_by = body.confirmed_by or actor
    gate = CallbackMappingGate(lambda report, _fp: report, name=confirmed_by)
    approved = gate.confirm(body.report, fingerprint)
    if approved is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="mapping declined")
    try:
        config = persist_confirmed_mapping(
            approved,
            config_dir=settings.CONFIG_DIR,
            confirmed_by=confirmed_by,
        )
    except Exception as exc:  # noqa: BLE001 - rejected mapping is a client error
        raise HTTPException(
            status_code=422,
            detail=f"mapping confirmation rejected: {exc}",
        ) from exc

    return MappingConfirmResponse(
        mapping_version=config.mapping_version, fingerprint=fingerprint
    )

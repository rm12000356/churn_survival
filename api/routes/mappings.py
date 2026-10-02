"""Mapping draft/confirm endpoints (ROADMAP Phase 8, Task 8.2).

``POST /mappings/confirm`` is the only way a mapping becomes configuration, and
it is always write-gated + authenticated. It routes the human-approved report
through :func:`orchestration.mapping.persist_confirmed_mapping` (the gate) and
never calls ``confirm_and_persist`` directly.

``POST /mappings/candidates`` screens every candidate model feature of a dataset
(architecture §1.8a) so the reviewer sees missingness, signal and leakage checks
next to each column. Confirm screens the approved features again: the client's
view of the verdict is never trusted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

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
    MappingCandidatesRequest,
    MappingCandidatesResponse,
    MappingConfirmRequest,
    MappingConfirmResponse,
    MappingDraftRequest,
)
from api.service import client_error_text, llm_client_or_none
from config.settings import Settings
from orchestration.mapping import CallbackMappingGate, persist_confirmed_mapping
from orchestration.routing import fingerprint_file
from router.llm_mapper import MappingAlreadyConfirmedError
from schemas.mapping import ApprovedFeature, FeatureScreening, MappingReport, SourceFingerprint

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


def _screen(
    settings: Settings, report: MappingReport, path: Path, keys: list[str] | None = None
) -> tuple[str, int, list[FeatureScreening]]:
    """Screen the candidate features of ``path`` under ``report`` (§1.8a).

    The evaluable rows are those Node 1 accepts with the report's identity and
    core mappings, under the Node 1 config a confirm would derive.
    """
    from config.loader import load_feature_screening_config, load_node2_config
    from node1.node import load_raw
    from router.feature_screening import screen_report
    from router.llm_mapper import _default_node1_config, derive_node1_config

    base_report = report.model_copy(
        update={
            "proposed_mappings": [
                m for m in report.proposed_mappings if not m.target_field.startswith("feature.")
            ]
        }
    )
    node1_config = derive_node1_config(base_report, _default_node1_config(settings.CONFIG_DIR))
    screening = load_feature_screening_config(config_root=settings.CONFIG_DIR)
    results = screen_report(
        report,
        load_raw(path),
        reference_date=settings.REFERENCE_DATE,
        node1_config=node1_config,
        screening=screening,
        keys=keys,
        node2_config=load_node2_config("1"),
    )
    n_evaluable = results[0].n_evaluable if results else 0
    return screening.screening_version, n_evaluable, results


def _bind(report: MappingReport, fingerprint: SourceFingerprint) -> MappingReport:
    """Bind a report to the dataset it is checked against; reject missing columns."""
    bound = report.model_copy(update={"source_fingerprint": fingerprint})
    missing = _missing_source_columns(bound, fingerprint)
    if missing:
        raise HTTPException(
            status_code=422,
            detail=(
                "mapping references columns that are not in this dataset: "
                + ", ".join(missing)
            ),
        )
    return bound


@router.post("/mappings/candidates", response_model=MappingCandidatesResponse)
def mapping_candidates(
    body: MappingCandidatesRequest,
    settings: Annotated[Settings, Depends(get_app_settings)],
    _writes: Annotated[None, Depends(require_writes)],
    _actor: Annotated[str, Depends(require_auth)],
) -> MappingCandidatesResponse:
    """Screen every candidate model feature of the dataset (deterministic, no LLM)."""
    path = resolve_raw_path(settings, body.raw_path)
    report = _bind(body.report, fingerprint_file(path))
    try:
        from router.llm_mapper import validate_mapping_report

        validate_mapping_report(report)
        version, n_evaluable, results = _screen(settings, report, path)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - an unusable mapping is a client error
        raise HTTPException(
            status_code=422, detail=f"feature screening failed: {client_error_text(exc)}"
        ) from exc
    return MappingCandidatesResponse(
        screening_version=version, n_evaluable=n_evaluable, candidates=results
    )


def _approved_features(
    settings: Settings, report: MappingReport, path: Path | None, keys: list[str]
) -> list[ApprovedFeature]:
    """Re-screen the approved features server-side; a blocked one is a 422."""
    if not keys:
        return []
    if path is None:
        raise HTTPException(
            status_code=422,
            detail="approving model features needs raw_path (features are screened on the data)",
        )
    proposals = {
        m.target_field[len("feature."):]: m
        for m in report.proposed_mappings
        if m.target_field.startswith("feature.")
    }
    unknown = sorted(set(keys) - set(proposals))
    if unknown:
        raise HTTPException(
            status_code=422,
            detail="approved features have no feature.<key> mapping: " + ", ".join(unknown),
        )
    _version, _n, results = _screen(settings, report, path, keys=sorted(set(keys)))
    blocked = [r for r in results if r.verdict == "block"]
    if blocked:
        raise HTTPException(
            status_code=422,
            detail="; ".join(
                f"{r.key} is blocked: {', '.join(r.block_reasons)}" for r in blocked
            ),
        )
    return [
        ApprovedFeature(
            key=r.key, kind=r.kind, source_column=r.source_column, label=r.source_column,
            screening=r,
        )
        for r in results
    ]


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
    path = resolve_raw_path(settings, body.raw_path) if body.raw_path is not None else None
    if fingerprint is None:
        if path is None:
            raise HTTPException(
                status_code=422,
                detail="provide either fingerprint or raw_path to confirm a mapping",
            )
        fingerprint = fingerprint_file(path)

    report = _bind(body.report, fingerprint)
    try:
        approved_features = _approved_features(settings, report, path, body.approved_features)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - an unusable mapping is a client error
        raise HTTPException(
            status_code=422,
            detail=f"mapping confirmation rejected: {client_error_text(exc)}",
        ) from exc

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
            approved_features=approved_features,
            supersedes=body.supersedes_mapping_version,
        )
    except MappingAlreadyConfirmedError as exc:
        detail: dict[str, Any] = {
            "message": str(exc),
            "existing_mapping_version": exc.mapping_version,
        }
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail) from exc
    except Exception as exc:  # noqa: BLE001 - rejected mapping is a client error
        raise HTTPException(
            status_code=422,
            detail=f"mapping confirmation rejected: {client_error_text(exc)}",
        ) from exc

    return MappingConfirmResponse(
        mapping_version=config.mapping_version, fingerprint=fingerprint
    )

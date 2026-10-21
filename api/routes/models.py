from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from api.deps import get_app_settings, safe_id
from api.schemas import ModelListResponse
from config.settings import Settings
from logging_setup import get_logger
from schemas.node2 import ModelArtifact

router = APIRouter(tags=["models"])


def _sidecar_path(settings: Settings, model_version: str) -> Path:
    safe_id(model_version, kind="model")
    return Path(settings.MODEL_DIR) / model_version / "model.json"


@router.get("/models", response_model=ModelListResponse)
def list_models(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ModelListResponse:
    base = Path(settings.MODEL_DIR)
    versions = (
        sorted(path.parent.name for path in base.glob("*/model.json"))
        if base.is_dir()
        else []
    )
    return ModelListResponse(models=versions, total=len(versions))


@router.get("/models/{model_version}", response_model=ModelArtifact)
def get_model(
    model_version: str,
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> ModelArtifact:
    path = _sidecar_path(settings, model_version)
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"unknown model {model_version!r}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return ModelArtifact.model_validate(payload)
    except (OSError, ValueError, ValidationError) as exc:
        get_logger(node="api").error(
            "model_artifact_unreadable", model_version=model_version
        )
        raise HTTPException(
            status_code=500,
            detail=f"model artifact {model_version!r} is unreadable",
        ) from exc

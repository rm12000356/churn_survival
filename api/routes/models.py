"""Model artifact endpoints (ROADMAP Phase 8).

Inspection only: these serve the persisted Node 2 ``ModelArtifact`` sidecars.
They never score or fit anything (that would be a decision, forbidden on read).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from api.deps import get_app_settings
from api.schemas import ModelListResponse
from config.settings import Settings
from schemas.node2 import ModelArtifact

router = APIRouter(tags=["models"])


def _sidecar_path(settings: Settings, model_version: str) -> Path:
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
    return ModelArtifact.model_validate(json.loads(path.read_text(encoding="utf-8")))

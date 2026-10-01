"""Health endpoint (ROADMAP Phase 8)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.deps import get_app_settings
from api.schemas import HealthResponse
from config.settings import Settings

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(settings: Settings = Depends(get_app_settings)) -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="churn-survival",
        writes_enabled=settings.API_ENABLE_WRITES,
        reference_date=settings.REFERENCE_DATE,
        llm_available=settings.LLM_PROVIDER != "none",
        llm_model=settings.LLM_MODEL if settings.LLM_PROVIDER != "none" else None,
    )

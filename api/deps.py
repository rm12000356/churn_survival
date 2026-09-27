"""FastAPI dependencies: settings/store providers + auth / write gates.

Policy (D-P7):

- ``API_KEY`` unset -> reads are open (demo), writes are disabled.
- ``API_KEY`` set   -> every endpoint requires the configured key header.
- Writes require ``API_ENABLE_WRITES=true`` **and** an ``API_KEY`` (the latter is
  enforced at ``Settings`` construction, so an accidentally-open write surface
  cannot start).
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from config.settings import Settings
from orchestration.persistence import RunStore

__all__ = [
    "actor_from_request",
    "get_store",
    "get_app_settings",
    "require_auth",
    "require_writes",
    "resolve_raw_path",
    "write_dependencies",
]


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_store(request: Request) -> RunStore:
    store: RunStore = request.app.state.store
    return store


def require_writes(request: Request) -> None:
    """Writes are disabled by default; enabling requires an API key (D-P7)."""
    settings: Settings = request.app.state.settings
    if not settings.API_ENABLE_WRITES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="writes are disabled (API_ENABLE_WRITES=false)",
        )


def require_auth(
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> str:
    """Enforce the configured API key when one is set; return the actor label."""
    settings: Settings = request.app.state.settings
    if settings.API_KEY:
        provided = request.headers.get(settings.API_KEY_HEADER, x_api_key)
        if not provided or not secrets.compare_digest(provided, settings.API_KEY):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or missing API key",
            )
    return actor_from_request(request)


def actor_from_request(request: Request) -> str:
    """The actor recorded in provenance (mapping ``confirmed_by``)."""
    return request.headers.get("X-Actor", "api-key")


def resolve_raw_path(settings: Settings, raw_path: str) -> Path:
    """Validate a server-side raw path; confine it to ``RAW_DATA_DIR`` (D-P7)."""
    path = Path(raw_path)
    if not path.is_file():
        raise HTTPException(
            status_code=422,
            detail=f"raw file not found: {raw_path}",
        )
    if not settings.API_ALLOW_ARBITRARY_PATHS:
        base = Path(settings.RAW_DATA_DIR).resolve()
        if not path.resolve().is_relative_to(base):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"raw_path must be under RAW_DATA_DIR ({base})",
            )
    return path


#: Dependency list for mutating endpoints: writes enabled + authenticated.
write_dependencies = [Depends(require_writes), Depends(require_auth)]

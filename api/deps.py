from __future__ import annotations

import hashlib
import re
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from config.settings import Settings
from logging_setup import get_logger
from orchestration.persistence import RunStore

__all__ = [
    "actor_from_request",
    "clean_label",
    "get_store",
    "get_app_settings",
    "require_auth",
    "require_writes",
    "resolve_raw_path",
    "safe_id",
    "write_dependencies",
]


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_store(request: Request) -> RunStore:
    store: RunStore = request.app.state.store
    return store


def require_writes(request: Request) -> None:
    settings: Settings = request.app.state.settings
    if not settings.API_ENABLE_WRITES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="writes are disabled (API_ENABLE_WRITES=false)",
        )


_API_KEY_SCHEME = APIKeyHeader(name="X-API-Key", auto_error=False)


def key_matches(settings: Settings, provided: str | None) -> bool:
    if not settings.API_KEY:
        return True
    return bool(provided) and secrets.compare_digest(
        str(provided).encode("utf-8"), settings.API_KEY.encode("utf-8")
    )


def client_key(request: Request) -> str:
    settings: Settings = request.app.state.settings
    who = key_fingerprint(settings.API_KEY) if settings.API_KEY else "open"
    host = request.client.host if request.client else "?"
    return f"{who}@{host}"


def require_auth(
    request: Request,
    x_api_key: Annotated[str | None, Security(_API_KEY_SCHEME)] = None,
) -> str:
    settings: Settings = request.app.state.settings
    if settings.API_KEY:
        provided = request.headers.get(settings.API_KEY_HEADER, x_api_key)
        if not key_matches(settings, provided):
            get_logger(node="api").warning("auth_failed", path=request.url.path)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid or missing API key",
            )
    return actor_from_request(request)


def key_fingerprint(api_key: str) -> str:
    return "api-key:" + hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]


def actor_from_request(request: Request) -> str:
    settings: Settings = request.app.state.settings
    authenticated = key_fingerprint(settings.API_KEY) if settings.API_KEY else "api-key"
    claimed = clean_label(request.headers.get("X-Actor"))
    return f"{claimed} ({authenticated})" if claimed else authenticated


def clean_label(value: str | None) -> str:
    if not value:
        return ""
    printable = "".join(ch for ch in value if ch.isprintable())
    return " ".join(printable.split())[:80]


_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def safe_id(value: str, *, kind: str) -> str:
    if not _SAFE_ID.match(value):
        raise HTTPException(status_code=404, detail=f"unknown {kind}")
    return value


def resolve_raw_path(settings: Settings, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.name == raw_path and raw_path not in {"", ".", ".."}:
        path = Path(settings.RAW_DATA_DIR) / raw_path
    resolved = path.resolve()
    if not settings.API_ALLOW_ARBITRARY_PATHS:
        base = Path(settings.RAW_DATA_DIR).resolve()
        if not resolved.is_relative_to(base):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="raw_path must be a file under RAW_DATA_DIR",
            )
    if not resolved.is_file():
        raise HTTPException(status_code=422, detail=f"raw file not found: {path.name}")
    return resolved


write_dependencies = [Depends(require_writes), Depends(require_auth)]

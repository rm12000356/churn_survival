"""API auth / write-gate tests (ROADMAP Phase 8, D-P7)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from config.settings import Settings


def _post(client: TestClient, raw_dir: Path, headers: dict[str, str] | None = None):
    return client.post(
        "/runs",
        json={"raw_path": str(raw_dir / "clean_customers.csv")},
        headers=headers,
    )


def test_writes_disabled_returns_403_but_reads_open(
    make_client, api_settings: Settings, raw_dir: Path
) -> None:
    settings = api_settings.model_copy(
        update={"API_ENABLE_WRITES": False, "API_KEY": None}
    )
    client = make_client(settings)
    assert client.get("/health").status_code == 200
    assert client.get("/runs").status_code == 200
    assert _post(client, raw_dir).status_code == 403


def test_missing_or_wrong_key_returns_401(
    make_client, api_settings: Settings, raw_dir: Path
) -> None:
    client = make_client(api_settings)
    assert _post(client, raw_dir).status_code == 401
    assert _post(client, raw_dir, headers={"X-API-Key": "wrong"}).status_code == 401
    assert _post(client, raw_dir, headers={"X-API-Key": "secret-key"}).status_code == 202


def test_writes_enabled_requires_key_at_startup(api_settings: Settings) -> None:
    payload = api_settings.model_dump()
    payload["API_ENABLE_WRITES"] = True
    payload["API_KEY"] = None
    with pytest.raises(ValidationError):
        Settings(**payload)

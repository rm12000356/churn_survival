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


def test_reads_require_the_key_when_one_is_set(
    make_client, api_settings: Settings
) -> None:
    """Regression (H1): with API_KEY set, reads are authenticated too."""
    client = make_client(api_settings)
    for path in ("/runs", "/runs/abc", "/models", "/raw-files", "/node1-configs"):
        assert client.get(path).status_code == 401, path
    assert client.get("/raw-files", headers={"X-API-Key": "secret-key"}).status_code == 200
    # Health and the static UI stay open so the browser can load and ask for a key.
    assert client.get("/health").status_code == 200


def test_non_ascii_key_is_401_not_500(make_client, api_settings: Settings) -> None:
    client = make_client(api_settings)
    # Raw non-ASCII bytes on the wire (str.compare_digest used to raise -> 500).
    response = client.get("/runs", headers={"X-API-Key": "clé-é".encode()})
    assert response.status_code == 401


def test_security_headers_are_set(make_client, api_settings: Settings) -> None:
    response = make_client(api_settings).get("/health")
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_oversized_json_body_is_rejected_before_parsing(
    make_client, api_settings: Settings
) -> None:
    client = make_client(api_settings)
    response = client.post(
        "/runs",
        content=b"{}",
        headers={
            "X-API-Key": "secret-key",
            "Content-Type": "application/json",
            "Content-Length": str(10**9),
        },
    )
    assert response.status_code == 413


def test_path_like_ids_and_versions_are_refused(
    make_client, api_settings: Settings, raw_dir: Path
) -> None:
    """Regression (M-S1/M-S2): ids and config versions are names, never paths."""
    client = make_client(api_settings)
    headers = {"X-API-Key": "secret-key"}
    for path in ("/runs/..%5C..%5Cx", "/runs/..%5Cx/report", "/models/..%5C..%5Cx"):
        assert client.get(path, headers=headers).status_code == 404, path
    body = {"raw_path": str(raw_dir / "clean_customers.csv"), "node2_version": "..\\..\\x"}
    assert client.post("/runs", json=body, headers=headers).status_code == 422


def test_raw_path_outside_raw_dir_does_not_reveal_existence(
    make_client, api_settings: Settings, tmp_path: Path
) -> None:
    """Regression (M-S7): confinement is checked before existence; no paths echoed."""
    client = make_client(api_settings)
    headers = {"X-API-Key": "secret-key"}
    outside = tmp_path / "outside.csv"
    outside.write_text("a\n1\n", encoding="utf-8")
    exists = client.post("/runs", json={"raw_path": str(outside)}, headers=headers)
    missing = client.post(
        "/runs", json={"raw_path": str(tmp_path / "nope.csv")}, headers=headers
    )
    assert exists.status_code == missing.status_code == 400
    assert str(tmp_path) not in exists.text


def test_writes_enabled_requires_key_at_startup(api_settings: Settings) -> None:
    payload = api_settings.model_dump()
    payload["API_ENABLE_WRITES"] = True
    payload["API_KEY"] = None
    with pytest.raises(ValidationError):
        Settings(**payload)

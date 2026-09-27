"""Mapping draft/confirm endpoint tests (ROADMAP Phase 8, D-O3)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from config.settings import Settings
from tests.mapping_helpers import mapping_payload


def test_draft_is_deterministic(
    client: TestClient, auth_headers: dict[str, str], raw_dir: Path
) -> None:
    body = {"raw_path": str(raw_dir / "unmapped_export.csv")}
    first = client.post("/mappings/draft", json=body, headers=auth_headers)
    second = client.post("/mappings/draft", json=body, headers=auth_headers)
    assert first.status_code == 200
    assert first.json()["source_fingerprint"] == second.json()["source_fingerprint"]
    assert first.json()["proposed_mappings"] == []


def test_confirm_requires_auth(
    client: TestClient, raw_dir: Path
) -> None:
    fingerprint = client.post(
        "/mappings/draft",
        json={"raw_path": str(raw_dir / "unmapped_export.csv")},
        headers={"X-API-Key": "secret-key"},
    ).json()["source_fingerprint"]
    response = client.post(
        "/mappings/confirm",
        json={"report": mapping_payload_from(fingerprint), "fingerprint": fingerprint},
    )
    assert response.status_code == 401


def mapping_payload_from(fingerprint: dict) -> dict:
    from schemas.mapping import SourceFingerprint

    return mapping_payload(SourceFingerprint.model_validate(fingerprint))


def test_confirm_persists_through_gate_then_routes(
    client: TestClient,
    auth_headers: dict[str, str],
    raw_dir: Path,
    api_settings: Settings,
) -> None:
    fingerprint = client.post(
        "/mappings/draft",
        json={"raw_path": str(raw_dir / "unmapped_export.csv")},
        headers=auth_headers,
    ).json()["source_fingerprint"]

    confirmed = client.post(
        "/mappings/confirm",
        json={
            "report": mapping_payload_from(fingerprint),
            "fingerprint": fingerprint,
            "confirmed_by": "qa-human",
        },
        headers=auth_headers,
    )
    assert confirmed.status_code == 200
    mapping_version = confirmed.json()["mapping_version"]
    assert mapping_version

    persisted = list((Path(api_settings.CONFIG_DIR) / "mappings").glob("map_*.json"))
    assert persisted

    # A stopped run can now route deterministically and complete.
    triggered = client.post(
        "/runs",
        json={"raw_path": str(raw_dir / "unmapped_export.csv")},
        headers=auth_headers,
    )
    assert triggered.status_code == 202
    run_id = triggered.json()["run_id"]
    assert client.get(f"/runs/{run_id}").json()["execution_status"] == "COMPLETED"


def test_confirm_requires_fingerprint_or_raw_path(
    client: TestClient, auth_headers: dict[str, str], raw_dir: Path
) -> None:
    fingerprint = client.post(
        "/mappings/draft",
        json={"raw_path": str(raw_dir / "unmapped_export.csv")},
        headers=auth_headers,
    ).json()["source_fingerprint"]
    response = client.post(
        "/mappings/confirm",
        json={"report": mapping_payload_from(fingerprint)},
        headers=auth_headers,
    )
    assert response.status_code == 422


def test_draft_with_llm_but_no_provider_is_422(
    client: TestClient, auth_headers: dict[str, str], raw_dir: Path
) -> None:
    response = client.post(
        "/mappings/draft",
        json={"raw_path": str(raw_dir / "unmapped_export.csv"), "use_llm": True},
        headers=auth_headers,
    )
    assert response.status_code == 422


def test_confirm_rejects_invalid_report(
    client: TestClient, auth_headers: dict[str, str], raw_dir: Path
) -> None:
    fingerprint = client.post(
        "/mappings/draft",
        json={"raw_path": str(raw_dir / "unmapped_export.csv")},
        headers=auth_headers,
    ).json()["source_fingerprint"]
    payload = mapping_payload_from(fingerprint)
    payload["proposed_mappings"][0]["transformation"] = "os.system('curl evil')"
    response = client.post(
        "/mappings/confirm",
        json={"report": payload, "fingerprint": fingerprint},
        headers=auth_headers,
    )
    assert response.status_code == 422

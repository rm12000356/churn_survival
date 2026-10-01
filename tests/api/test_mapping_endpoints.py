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
    client: TestClient, anon_client: TestClient, raw_dir: Path
) -> None:
    fingerprint = client.post(
        "/mappings/draft",
        json={"raw_path": str(raw_dir / "unmapped_export.csv")},
        headers={"X-API-Key": "secret-key"},
    ).json()["source_fingerprint"]
    response = anon_client.post(
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


def _stale_fingerprint(fingerprint: dict) -> dict:
    """A mapping file written for some other export (different headers_hash)."""
    return {**fingerprint, "headers_hash": "0" * 64}


def test_uploaded_mapping_is_bound_to_the_dataset_it_confirms(
    client: TestClient,
    auth_headers: dict[str, str],
    raw_dir: Path,
    api_settings: Settings,
) -> None:
    raw_path = str(raw_dir / "unmapped_export.csv")
    fingerprint = client.post(
        "/mappings/draft", json={"raw_path": raw_path}, headers=auth_headers
    ).json()["source_fingerprint"]
    uploaded = mapping_payload_from(fingerprint)
    uploaded["source_fingerprint"] = _stale_fingerprint(fingerprint)

    confirmed = client.post(
        "/mappings/confirm",
        json={"report": uploaded, "raw_path": raw_path},
        headers=auth_headers,
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["fingerprint"]["headers_hash"] == fingerprint["headers_hash"]

    # The persisted mapping routes THIS dataset, so the run completes.
    triggered = client.post("/runs", json={"raw_path": raw_path}, headers=auth_headers)
    assert triggered.status_code == 202
    run_id = triggered.json()["run_id"]
    assert client.get(f"/runs/{run_id}").json()["execution_status"] == "COMPLETED"


def test_confirm_rejects_mapping_for_columns_the_dataset_lacks(
    client: TestClient, auth_headers: dict[str, str], raw_dir: Path
) -> None:
    raw_path = str(raw_dir / "unmapped_export.csv")
    fingerprint = client.post(
        "/mappings/draft", json={"raw_path": raw_path}, headers=auth_headers
    ).json()["source_fingerprint"]
    payload = mapping_payload_from(fingerprint)
    payload["proposed_mappings"][0]["source_column"] = "Kundennummer"
    response = client.post(
        "/mappings/confirm",
        json={"report": payload, "raw_path": raw_path},
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert "Kundennummer" in response.json()["detail"]


def test_second_confirm_for_same_dataset_is_409_and_runs_still_work(
    client: TestClient, auth_headers: dict[str, str], raw_dir: Path
) -> None:
    raw_path = str(raw_dir / "unmapped_export.csv")
    fingerprint = client.post(
        "/mappings/draft", json={"raw_path": raw_path}, headers=auth_headers
    ).json()["source_fingerprint"]
    body = {"report": mapping_payload_from(fingerprint), "raw_path": raw_path}
    first = client.post("/mappings/confirm", json=body, headers=auth_headers)
    assert first.status_code == 200

    second = client.post("/mappings/confirm", json=body, headers=auth_headers)
    assert second.status_code == 409
    assert first.json()["mapping_version"] in second.json()["detail"]

    triggered = client.post("/runs", json={"raw_path": raw_path}, headers=auth_headers)
    assert triggered.status_code == 202


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

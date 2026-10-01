"""Trigger endpoint tests: async semantics + the status matrix (D-P4/D-P11)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from orchestration.persistence import RunStore
from schemas.run import RunExecutionStatus, RunSummary


def _body(raw_dir: Path, name: str = "clean_customers.csv") -> dict:
    return {"raw_path": str(raw_dir / name)}


def _trigger(client: TestClient, headers: dict[str, str], raw_dir: Path, **overrides):
    body = _body(raw_dir)
    body.update(overrides)
    return client.post("/runs", json=body, headers=headers)


def test_trigger_returns_202_then_completes(
    client: TestClient, auth_headers: dict[str, str], raw_dir: Path
) -> None:
    response = _trigger(client, auth_headers, raw_dir)
    assert response.status_code == 202
    run_id = response.json()["run_id"]

    detail = client.get(f"/runs/{run_id}").json()
    assert detail["execution_status"] == "COMPLETED"
    assert detail["pipeline_status"] == "COMPLETED"


def test_idempotent_completed_returns_200_cached(
    client: TestClient, auth_headers: dict[str, str], raw_dir: Path
) -> None:
    first = _trigger(client, auth_headers, raw_dir)
    run_id = first.json()["run_id"]
    second = _trigger(client, auth_headers, raw_dir)
    assert second.status_code == 200
    assert second.json()["run_id"] == run_id
    assert second.json()["execution_status"] == "COMPLETED"


def test_unmapped_stops_then_cached_and_force_rejected(
    client: TestClient, auth_headers: dict[str, str], raw_dir: Path
) -> None:
    unmapped = {"raw_path": str(raw_dir / "unmapped_export.csv")}
    response = _trigger(client, auth_headers, raw_dir, **unmapped)
    assert response.status_code == 202
    run_id = response.json()["run_id"]

    detail = client.get(f"/runs/{run_id}").json()
    assert detail["execution_status"] == "STOPPED_NEEDS_MAPPING"
    assert detail["pending_fingerprint"] is not None

    cached = _trigger(client, auth_headers, raw_dir, **unmapped)
    assert cached.status_code == 200
    assert cached.json()["run_id"] == run_id

    forced = client.post(
        "/runs?force=true",
        json=_body(raw_dir, "unmapped_export.csv"),
        headers=auth_headers,
    )
    assert forced.status_code == 400


def test_failed_run_resubmits_in_place(
    client: TestClient, auth_headers: dict[str, str], raw_dir: Path, store: RunStore
) -> None:
    run_id = _trigger(client, auth_headers, raw_dir).json()["run_id"]
    summary = store.get_summary(run_id)
    assert summary is not None
    store.index.upsert(
        summary.model_copy(update={"execution_status": RunExecutionStatus.FAILED})
    )

    retry = _trigger(client, auth_headers, raw_dir)
    assert retry.status_code == 202
    assert retry.json()["run_id"] == run_id
    assert client.get(f"/runs/{run_id}").json()["execution_status"] == "COMPLETED"


def test_running_force_conflict(
    client: TestClient, auth_headers: dict[str, str], raw_dir: Path, store: RunStore
) -> None:
    run_id = _trigger(client, auth_headers, raw_dir).json()["run_id"]
    summary = store.get_summary(run_id)
    assert summary is not None
    store.index.upsert(
        summary.model_copy(update={"execution_status": RunExecutionStatus.RUNNING})
    )

    assert (
        client.post("/runs?force=true", json=_body(raw_dir), headers=auth_headers).status_code
        == 409
    )
    assert _trigger(client, auth_headers, raw_dir).status_code == 202


def test_supersedes_linkage_recorded(
    client: TestClient, auth_headers: dict[str, str], raw_dir: Path, store: RunStore
) -> None:
    run_id = _trigger(client, auth_headers, raw_dir).json()["run_id"]
    store.index.upsert(
        RunSummary(
            run_id="old-run",
            execution_status=RunExecutionStatus.STOPPED_NEEDS_MAPPING,
        )
    )

    forced = client.post(
        "/runs?force=true",
        json={**_body(raw_dir), "supersedes_run_id": "old-run"},
        headers=auth_headers,
    )
    assert forced.status_code == 202
    old = store.get_summary("old-run")
    assert old is not None
    assert old.superseded_by == run_id

"""Run lifecycle regressions through the API (H5, H7, L3, L4)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestration.persistence import RunStore


def _trigger(client: TestClient, raw: Path, **extra: object):
    return client.post("/runs", json={"raw_path": str(raw), **extra})


def test_completed_run_keeps_its_timestamps(client: TestClient, clean_csv: Path) -> None:
    """H5: history ordering needs created_at on finished runs."""
    accepted = _trigger(client, clean_csv)
    assert accepted.status_code == 202
    assert accepted.json()["execution_status"] == "PENDING"  # L3: queued, not running
    summary = client.get(f"/runs/{accepted.json()['run_id']}").json()
    assert summary["execution_status"] == "COMPLETED"
    assert summary["created_at"] and summary["started_at"] and summary["finished_at"]


def test_persistence_failure_marks_the_run_failed(
    client: TestClient, clean_csv: Path, store: RunStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """H7: a failing save must not leave the run RUNNING forever."""

    def broken_save(_result: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store, "save", broken_save)
    run_id = _trigger(client, clean_csv).json()["run_id"]
    summary = client.get(f"/runs/{run_id}").json()
    assert summary["execution_status"] == "FAILED"
    assert summary["error_code"] == "PERSIST_ERROR"


def test_run_cannot_supersede_itself(client: TestClient, clean_csv: Path) -> None:
    run_id = _trigger(client, clean_csv).json()["run_id"]
    forced = client.post(
        "/runs?force=true", json={"raw_path": str(clean_csv), "supersedes_run_id": run_id}
    )
    assert forced.status_code == 422


def test_run_list_total_counts_all_matches(client: TestClient, clean_csv: Path) -> None:
    _trigger(client, clean_csv)
    _trigger(client, clean_csv, reference_date="2026-08-16")
    listing = client.get("/runs?limit=1").json()
    assert len(listing["runs"]) == 1
    assert listing["total"] == 2

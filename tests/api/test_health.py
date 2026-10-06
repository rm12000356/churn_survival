"""Health endpoint tests (ROADMAP Phase 8)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_is_minimal_liveness(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "service": "churn-survival"}


def test_status_reports_posture_when_reads_open(client: TestClient) -> None:
    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "churn-survival"
    assert body["writes_enabled"] is True
    assert body["reference_date"] == "2026-08-15"

"""Health endpoint tests (ROADMAP Phase 8)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "churn-survival"
    assert body["writes_enabled"] is True
    assert body["reference_date"] == "2026-08-15"

"""Model artifact endpoint tests (ROADMAP Phase 8)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from config.settings import Settings


def _sidecar(model_version: str) -> dict:
    return {
        "model_version": model_version,
        "training_timestamp": datetime(2026, 8, 15, tzinfo=UTC).isoformat(),
        "training_dataset_version": "ds",
        "reference_date": date(2026, 8, 15).isoformat(),
        "selected_features": ["plan_tier"],
        "coefficients": {"plan_tier": 0.1},
        "baseline": {},
        "penalizer": 0.01,
        "n_customers": 10,
        "n_events": 2,
        "encoding_scheme": {},
        "validation_metrics": {},
        "assumption_check_results": {},
        "horizon_config": [30, 90, 180],
    }


def test_models_empty(client: TestClient) -> None:
    response = client.get("/models")
    assert response.status_code == 200
    assert response.json() == {"models": [], "total": 0}


def test_model_served_from_sidecar(client: TestClient, api_settings: Settings) -> None:
    version = "abc123"
    target = Path(api_settings.MODEL_DIR) / version
    target.mkdir(parents=True)
    (target / "model.json").write_text(json.dumps(_sidecar(version)), encoding="utf-8")

    listing = client.get("/models").json()
    assert listing == {"models": [version], "total": 1}

    got = client.get(f"/models/{version}")
    assert got.status_code == 200
    assert got.json()["model_version"] == version


def test_unknown_model_404(client: TestClient) -> None:
    assert client.get("/models/nope").status_code == 404

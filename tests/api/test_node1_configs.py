"""Node 1 deployment-config listing + auto-resolution (Horizon additive).

``GET /node1-configs`` is read-only; ``POST /runs`` still triggers exactly one
computation. A run triggered without ``node1_version`` defaults to ``"auto"`` and
resolves the deployment config from the matched confirmed mapping.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def test_list_node1_configs(client: TestClient) -> None:
    response = client.get("/node1-configs")
    assert response.status_code == 200
    body = response.json()
    versions = {config["version"] for config in body["configs"]}
    assert {"1", "bank", "credit", "cellular", "telco", "dataset7"} <= versions
    bank = next(config for config in body["configs"] if config["version"] == "bank")
    assert bank["approved_core_keys"] == []
    assert bank["allow_missing_core_passthrough"] is False
    assert body["total"] == len(body["configs"])


def test_trigger_run_defaults_to_auto(
    client: TestClient, clean_csv: Path, auth_headers: dict[str, str]
) -> None:
    # No node1_version supplied: the API defaults to "auto"; a built-in adapter
    # resolves to the default v1 and the run completes.
    response = client.post(
        "/runs",
        json={"raw_path": str(clean_csv)},
        headers=auth_headers,
    )
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    detail = client.get(f"/runs/{run_id}").json()
    assert detail["execution_status"] == "COMPLETED"

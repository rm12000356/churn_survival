"""E2E: stop -> confirm mapping -> retry yields a new, completing run identity.

Proves the Phase 7/frozen mapping registry and the content-addressed run_id do
not collide (D-P1): confirming a mapping changes the resolved routing identity,
so the retry is a *different* run, and the stopped run survives as audit.

ROADMAP Task 8.3.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from config.settings import Settings
from schemas.mapping import SourceFingerprint
from tests.e2e.conftest import FIXTURES
from tests.mapping_helpers import mapping_payload


def _post_run(client: TestClient, headers: dict[str, str], raw_path: Path, **extra):
    body = {"raw_path": str(raw_path)}
    body.update(extra)
    return client.post("/runs", json=body, headers=headers)


def test_stop_confirm_retry_new_identity(
    client: TestClient,
    auth_headers: dict[str, str],
    e2e_settings: Settings,
) -> None:
    raw_dir = Path(e2e_settings.RAW_DATA_DIR)
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / "unmapped_export.csv"
    shutil.copy(FIXTURES / "unmapped_export.csv", raw_path)

    first = _post_run(client, auth_headers, raw_path)
    assert first.status_code == 202
    x0 = first.json()["run_id"]

    stopped = client.get(f"/runs/{x0}").json()
    assert stopped["execution_status"] == "STOPPED_NEEDS_MAPPING"
    fingerprint = SourceFingerprint.model_validate(stopped["pending_fingerprint"])
    # X0 never ran Node 1.
    assert client.get(f"/runs/{x0}/node1").status_code == 404

    confirmed = client.post(
        "/mappings/confirm",
        json={
            "report": mapping_payload(fingerprint),
            "fingerprint": fingerprint.model_dump(mode="json"),
            "confirmed_by": "e2e-human",
        },
        headers=auth_headers,
    )
    assert confirmed.status_code == 200

    retry = _post_run(client, auth_headers, raw_path, supersedes_run_id=x0)
    assert retry.status_code == 202
    x1 = retry.json()["run_id"]
    assert x1 != x0

    completed = client.get(f"/runs/{x1}").json()
    assert completed["execution_status"] == "COMPLETED"
    # X1 ran Node 1 (and beyond); X0 did not.
    assert client.get(f"/runs/{x1}/node1").status_code == 200

    # The stopped run is retained as audit and linked to its successor.
    original = client.get(f"/runs/{x0}").json()
    assert original["execution_status"] == "STOPPED_NEEDS_MAPPING"
    assert original["superseded_by"] == x1

    # Routing is stable: re-triggering the now-routable input reuses X1.
    again = _post_run(client, auth_headers, raw_path)
    assert again.status_code == 200
    assert again.json()["run_id"] == x1

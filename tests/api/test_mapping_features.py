"""API: candidate screening + confirming model features (architecture §1.3a/§1.8a)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from router.fingerprint import extract_fingerprint
from tests.router.test_declared_features import _feature, _frame, _report


def _dataset(raw_dir: Path) -> tuple[str, dict]:
    frame = _frame(1200)
    path = raw_dir / "snapshot.csv"
    frame.to_csv(path, index=False)
    report = _report(frame, [_feature("Complain", "number"), _feature("PerfectScore", "number")])
    report = report.model_copy(
        update={"source_fingerprint": extract_fingerprint(pd.read_csv(path))}
    )
    return path.name, report.model_dump(mode="json")


def test_candidates_screen_every_column(client: TestClient, raw_dir: Path) -> None:
    raw_path, report = _dataset(raw_dir)
    response = client.post("/mappings/candidates", json={"report": report, "raw_path": raw_path})
    assert response.status_code == 200, response.text
    body = response.json()
    by_column = {c["source_column"]: c for c in body["candidates"]}
    assert body["screening_version"] == "1"
    assert by_column["Complain"]["verdict"] == "ok"
    assert by_column["Complain"]["ph_p_value"] is not None
    assert by_column["PerfectScore"]["verdict"] == "block"
    assert by_column["Segment"]["kind"] == "category"  # unmapped column, inferred kind
    assert body["n_evaluable"] == by_column["Complain"]["n_evaluable"] > 0


def test_confirm_with_features_derives_declared_config(
    client: TestClient, raw_dir: Path, config_dir: Path
) -> None:
    raw_path, report = _dataset(raw_dir)
    blocked = client.post(
        "/mappings/confirm",
        json={"report": report, "raw_path": raw_path, "approved_features": ["perfect_score"]},
    )
    assert blocked.status_code == 422
    assert "blocked" in blocked.json()["detail"]

    confirmed = client.post(
        "/mappings/confirm",
        json={"report": report, "raw_path": raw_path, "approved_features": ["complain"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    version = confirmed.json()["mapping_version"]
    stored = json.loads((config_dir / "mappings" / f"{version}.json").read_text())
    assert [f["key"] for f in stored["approved_features"]] == ["complain"]
    assert stored["approved_features"][0]["screening"]["verdict"] == "ok"
    node1 = json.loads((config_dir / "node1" / f"v{version}.json").read_text())
    assert node1["declared_features"] == {"complain": {"kind": "number", "label": "Complain"}}

    # Re-mapping the same shape: 409 names the active mapping, supersede replaces it.
    again = client.post("/mappings/confirm", json={"report": report, "raw_path": raw_path})
    assert again.status_code == 409
    assert again.json()["detail"]["existing_mapping_version"] == version
    replaced = client.post(
        "/mappings/confirm",
        json={"report": report, "raw_path": raw_path, "supersedes_mapping_version": version},
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["mapping_version"] != version


def test_approving_features_needs_the_dataset(client: TestClient, raw_dir: Path) -> None:
    _raw_path, report = _dataset(raw_dir)
    response = client.post(
        "/mappings/confirm",
        json={
            "report": report,
            "fingerprint": report["source_fingerprint"],
            "approved_features": ["complain"],
        },
    )
    assert response.status_code == 422
    assert "raw_path" in response.json()["detail"]

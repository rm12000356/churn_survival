"""E2E: a workbook with a dictionary sheet first is onboarded by confirming a mapping.

The user's path with no hand-made config: upload a workbook whose first sheet is
a data dictionary -> the run stops for a mapping -> confirm a mapping that maps
only the identity fields, with no Node 1 config chosen -> the retry completes.
Before the fix the draft fingerprinted the dictionary sheet, and the retry used
``v1`` and failed the whole batch on core keys the dataset never had.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from config.settings import Settings
from schemas.mapping import SourceFingerprint
from tests.e2e.conftest import FIXTURES
from tests.router.test_primary_sheet_and_derived_config import identity_only_payload


def test_workbook_confirm_without_node1_config_completes(
    client: TestClient,
    auth_headers: dict[str, str],
    e2e_settings: Settings,
) -> None:
    raw_dir = Path(e2e_settings.RAW_DATA_DIR)
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / "export_with_dictionary.xlsx"
    data = pd.read_csv(FIXTURES / "unmapped_export.csv")
    with pd.ExcelWriter(raw_path) as writer:
        pd.DataFrame({"Variable": ["Cust ID"], "Meaning": ["customer id"]}).to_excel(
            writer, sheet_name="Data Dict", index=False
        )
        data.to_excel(writer, sheet_name="Export", index=False)

    first = client.post("/runs", json={"raw_path": str(raw_path)}, headers=auth_headers)
    assert first.status_code == 202
    stopped = client.get(f"/runs/{first.json()['run_id']}").json()
    assert stopped["execution_status"] == "STOPPED_NEEDS_MAPPING"
    fingerprint = SourceFingerprint.model_validate(stopped["pending_fingerprint"])
    assert fingerprint.primary_sheet == "Export"
    assert fingerprint.column_names == list(data.columns)

    confirmed = client.post(
        "/mappings/confirm",
        json={
            "report": identity_only_payload(fingerprint),
            "fingerprint": fingerprint.model_dump(mode="json"),
            "confirmed_by": "e2e-human",
        },
        headers=auth_headers,
    )
    assert confirmed.status_code == 200
    mapping_version = confirmed.json()["mapping_version"]
    assert (Path(e2e_settings.CONFIG_DIR) / "node1" / f"v{mapping_version}.json").is_file()

    retry = client.post("/runs", json={"raw_path": str(raw_path)}, headers=auth_headers)
    assert retry.status_code == 202
    completed = client.get(f"/runs/{retry.json()['run_id']}").json()
    assert completed["execution_status"] == "COMPLETED", completed
    node1 = client.get(f"/runs/{retry.json()['run_id']}/node1").json()
    report = node1["validation_report"]
    assert report["status"] in {"PASSED", "PARTIAL"}
    assert report["n_accepted"] == len(data)

"""E2E: the API serves a persisted dataset-7 run without recomputing.

ROADMAP Task 8.3.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.usefixtures("dataset7_corpus")


def test_api_serves_persisted_dataset7(client: TestClient, seed_dataset7) -> None:
    result = seed_dataset7()
    run_id = result.state.run_id
    node4 = result.state.node4_output
    assert node4 is not None

    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["execution_status"] == "COMPLETED"

    report = client.get(f"/runs/{run_id}/report").json()
    distribution = report["report"]["risk_distribution"]
    assert distribution["critical"] == node4.summary_stats.n_critical
    assert distribution["insufficient_data"] == node4.summary_stats.n_insufficient_data
    assert [a["customer_id"] for a in report["report"]["priority_accounts"]] == [
        a.customer_id for a in node4.ranked_accounts
    ]

    ranked = client.get(f"/runs/{run_id}/ranked-accounts").json()
    assert ranked["summary_stats"]["n_critical"] == node4.summary_stats.n_critical

    html = client.get(f"/runs/{run_id}/report.html")
    assert html.status_code == 200
    assert "<html" in html.text.lower()

    # Reads are idempotent.
    assert (
        client.get(f"/runs/{run_id}/report").content
        == client.get(f"/runs/{run_id}/report").content
    )

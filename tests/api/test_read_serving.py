"""Read-endpoint tests: serving is read-only and never recomputes (D-P3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from orchestration.persistence import RunStore


def _seed(seed_run, raw_dir: Path) -> str:
    result = seed_run(raw_dir / "clean_customers.csv")
    return result.state.run_id


def test_list_and_detail(client: TestClient, seed_run, raw_dir: Path) -> None:
    run_id = _seed(seed_run, raw_dir)
    listing = client.get("/runs")
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["runs"][0]["run_id"] == run_id

    detail = client.get(f"/runs/{run_id}")
    assert detail.status_code == 200
    assert detail.json()["execution_status"] == "COMPLETED"


def test_read_all_outputs(client: TestClient, seed_run, raw_dir: Path) -> None:
    run_id = _seed(seed_run, raw_dir)
    for path in (
        f"/runs/{run_id}/report",
        f"/runs/{run_id}/report.html",
        f"/runs/{run_id}/ranked-accounts",
        f"/runs/{run_id}/node1",
        f"/runs/{run_id}/node2",
        f"/runs/{run_id}/node3",
        f"/runs/{run_id}/node4",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
    assert "<html" in client.get(f"/runs/{run_id}/report.html").text.lower()


def test_reads_are_idempotent(client: TestClient, seed_run, raw_dir: Path) -> None:
    run_id = _seed(seed_run, raw_dir)
    first = client.get(f"/runs/{run_id}/report").content
    second = client.get(f"/runs/{run_id}/report").content
    assert first == second


def test_reads_never_recompute(
    client: TestClient, seed_run, raw_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No GET may call a node entry point, even for a stored run (D-P3)."""
    run_id = _seed(seed_run, raw_dir)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("read endpoint attempted to recompute a decision")

    import node1.node as node1
    import node2.node as node2
    import node3.node as node3
    import node4.node as node4
    import node5.node as node5

    monkeypatch.setattr(node1, "run_node1", _boom)
    monkeypatch.setattr(node2, "run_node2", _boom)
    monkeypatch.setattr(node2, "fit_model", _boom)
    monkeypatch.setattr(node2, "score_to_output", _boom)
    monkeypatch.setattr(node3, "run_node3", _boom)
    monkeypatch.setattr(node4, "run_node4", _boom)
    monkeypatch.setattr(node5, "run_node5", _boom)

    for path in (
        "/runs",
        f"/runs/{run_id}",
        f"/runs/{run_id}/report",
        f"/runs/{run_id}/report.html",
        f"/runs/{run_id}/ranked-accounts",
        f"/runs/{run_id}/node4",
    ):
        assert client.get(path).status_code == 200, path


def test_missing_run_404(client: TestClient, store: RunStore) -> None:
    assert client.get("/runs/does-not-exist").status_code == 404
    assert client.get("/runs/does-not-exist/report").status_code == 404
    assert client.get("/runs/does-not-exist/report.html").status_code == 404
    assert client.get("/runs/does-not-exist/node1").status_code == 404


def test_missing_node_output_404(client: TestClient, seed_run, raw_dir: Path) -> None:
    """A run that stopped before a node has no output for it (but is known)."""
    run_id = _seed(seed_run, raw_dir)
    # node1 exists; ask for a node that does not exist in the store.
    assert client.get(f"/runs/{run_id}/node9").status_code == 404

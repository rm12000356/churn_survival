"""REVIEW §6 API hardening: N-H6, N-M2, N-M3, N-M15, N-M16 and the API LOW items."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import api.app as api_app
from api.app import create_app
from config.settings import Settings
from orchestration.persistence import RunStore
from schemas.run import RunExecutionStatus, RunSummary
from tests.api.conftest import API_KEY, SyncExecutor

KEY = {"X-API-Key": API_KEY}


def _client(settings: Settings, executor: Any | None = None) -> TestClient:
    application = create_app(
        settings,
        store=RunStore(settings.RUN_DIR),
        executor=executor or SyncExecutor(),
        run_startup_recovery=False,
    )
    return TestClient(application)


# --- N-H6: reject before the body is read ---------------------------------------


def _chunks(total: int, chunk: int = 64 * 1024):
    sent = 0
    while sent < total:
        size = min(chunk, total - sent)
        sent += size
        yield b"x" * size


def test_unauthenticated_write_is_rejected_without_reading_the_body(
    api_settings: Settings,
) -> None:
    consumed = []

    def body():
        for part in _chunks(2 * 1024 * 1024):
            consumed.append(len(part))
            yield part

    with _client(api_settings) as client:
        response = client.post("/uploads", content=body(), headers={"Content-Type": "x/y"})
    assert response.status_code == 401
    assert sum(consumed) < 2 * 1024 * 1024  # never streamed to the end


def test_writes_disabled_rejects_before_body(api_settings: Settings) -> None:
    settings = api_settings.model_copy(update={"API_ENABLE_WRITES": False})
    with _client(settings) as client:
        response = client.post("/runs", content=_chunks(1024), headers=KEY)
    assert response.status_code == 403


def test_chunked_body_over_limit_is_cut_off(
    api_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No Content-Length (chunked): the guard counts received bytes instead.
    monkeypatch.setattr(api_app, "_MAX_JSON_BYTES", 4096)
    with _client(api_settings) as client:
        response = client.post(
            "/runs",
            content=_chunks(64 * 1024, chunk=1024),
            headers={**KEY, "Content-Type": "application/json"},
        )
    assert response.status_code == 413


def test_declared_oversized_body_is_rejected(
    api_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(api_app, "_MAX_JSON_BYTES", 10)
    with _client(api_settings) as client:
        response = client.post("/runs", json={"raw_path": "clean_customers.csv"}, headers=KEY)
    assert response.status_code == 413


class _HoldingExecutor:
    """Accepts jobs but never runs them: every queued run stays in flight."""

    def __init__(self) -> None:
        self.jobs: list[Any] = []

    def submit(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        self.jobs.append((fn, args, kwargs))

        class _Pending:
            def add_done_callback(self, _cb: Any) -> None:
                return None

        return _Pending()

    def shutdown(self, *args: Any, **kwargs: Any) -> None:
        return None


def test_queue_cap_answers_429(api_settings: Settings, raw_dir: Path) -> None:
    settings = api_settings.model_copy(update={"RUN_MAX_QUEUED": 1})
    with _client(settings, executor=_HoldingExecutor()) as client:
        first = client.post("/runs", json={"raw_path": "clean_customers.csv"}, headers=KEY)
        second = client.post("/runs", json={"raw_path": "unmapped_export.csv"}, headers=KEY)
    assert first.status_code == 202
    assert second.status_code == 429


def test_trigger_rate_limit_answers_429(api_settings: Settings) -> None:
    settings = api_settings.model_copy(update={"RUN_TRIGGERS_PER_MINUTE": 1})
    with _client(settings) as client:
        first = client.post("/runs", json={"raw_path": "clean_customers.csv"}, headers=KEY)
        second = client.post("/runs", json={"raw_path": "clean_customers.csv"}, headers=KEY)
    assert first.status_code == 202
    assert second.status_code == 429
    assert second.headers.get("Retry-After") == "60"


def test_upload_rate_limit_answers_429(api_settings: Settings) -> None:
    settings = api_settings.model_copy(update={"UPLOADS_PER_MINUTE": 1})
    with _client(settings) as client:
        files = {"file": ("a.csv", b"customer_id\n1\n", "text/csv")}
        assert client.post("/uploads", files=files, headers=KEY).status_code == 200
        files = {"file": ("b.csv", b"customer_id\n2\n", "text/csv")}
        assert client.post("/uploads", files=files, headers=KEY).status_code == 429


# --- N-M3: a failed submit never leaves a PENDING row --------------------------


class _BrokenExecutor:
    def submit(self, *_a: Any, **_k: Any) -> Any:
        raise RuntimeError("cannot schedule new futures after shutdown")

    def shutdown(self, *args: Any, **kwargs: Any) -> None:
        return None


def test_submit_failure_marks_failed_and_returns_503(api_settings: Settings) -> None:
    with _client(api_settings, executor=_BrokenExecutor()) as client:
        response = client.post("/runs", json={"raw_path": "clean_customers.csv"}, headers=KEY)
    assert response.status_code == 503
    store = RunStore(api_settings.RUN_DIR)
    rows = store.list_runs()
    assert len(rows) == 1
    assert rows[0].execution_status is RunExecutionStatus.FAILED
    assert rows[0].error_code == "ENQUEUE_FAILED"
    # A later trigger resubmits it (FAILED is not treated as in flight).
    with _client(api_settings) as client:
        retry = client.post("/runs", json={"raw_path": "clean_customers.csv"}, headers=KEY)
    assert retry.status_code == 202


# --- N-M2: failures keep a client-safe message ---------------------------------


def test_pipeline_exception_records_a_message(
    api_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    import orchestration.graph as graph

    def boom(*_a: Any, **_k: Any) -> Any:
        raise ValueError("synthetic pipeline failure")

    monkeypatch.setattr(graph, "run_pipeline", boom)
    with _client(api_settings) as client:
        run_id = client.post(
            "/runs", json={"raw_path": "clean_customers.csv"}, headers=KEY
        ).json()["run_id"]
        detail = client.get(f"/runs/{run_id}", headers=KEY).json()
    assert detail["execution_status"] == "FAILED"
    assert detail["error_code"] == "ValueError"
    assert detail["errors"] and detail["errors"][0]["message"]


def test_persist_failure_is_reported_as_persist_error(
    api_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_save(self: RunStore, result: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(RunStore, "save", failing_save)
    with _client(api_settings) as client:
        run_id = client.post(
            "/runs", json={"raw_path": "clean_customers.csv"}, headers=KEY
        ).json()["run_id"]
        detail = client.get(f"/runs/{run_id}", headers=KEY).json()
    assert detail["execution_status"] == "FAILED"
    assert detail["error_code"] == "PERSIST_ERROR"


# --- N-M16 / LOW: paging, unknown supersedes, corrupt stored output ------------------


def test_runs_can_be_paged_with_offset(api_settings: Settings) -> None:
    store = RunStore(api_settings.RUN_DIR)
    for i in range(5):
        store.index.upsert(
            RunSummary(
                run_id=f"run{i:02d}",
                execution_status=RunExecutionStatus.COMPLETED,
                created_at=f"2026-08-0{i + 1}T00:00:00Z",
            )
        )
    with _client(api_settings) as client:
        page1 = client.get("/runs?limit=2", headers=KEY).json()
        page3 = client.get("/runs?limit=2&offset=4", headers=KEY).json()
    assert [r["run_id"] for r in page1["runs"]] == ["run04", "run03"]
    assert [r["run_id"] for r in page3["runs"]] == ["run00"]
    assert page1["total"] == page3["total"] == 5


def test_unknown_supersedes_run_is_422(api_settings: Settings) -> None:
    with _client(api_settings) as client:
        response = client.post(
            "/runs",
            json={"raw_path": "clean_customers.csv", "supersedes_run_id": "nosuchrun"},
            headers=KEY,
        )
    assert response.status_code == 422


def test_corrupt_stored_output_is_a_structured_500(api_settings: Settings) -> None:
    with _client(api_settings) as client:
        run_id = client.post(
            "/runs", json={"raw_path": "clean_customers.csv"}, headers=KEY
        ).json()["run_id"]
        (Path(api_settings.RUN_DIR) / run_id / "node4.json").write_text(
            '{"not": "a node4 output"}', encoding="utf-8"
        )
        response = client.get(f"/runs/{run_id}/ranked-accounts", headers=KEY)
    assert response.status_code == 500
    assert "unreadable" in response.json()["detail"]


def test_openapi_declares_the_api_key_scheme(api_settings: Settings) -> None:
    with _client(api_settings) as client:
        schema = client.get("/openapi.json").json()
    schemes = schema["components"]["securitySchemes"]
    assert any(s.get("type") == "apiKey" and s.get("in") == "header" for s in schemes.values())
    assert "429" in schema["paths"]["/runs"]["post"]["responses"]


def test_raw_file_listing_does_not_disclose_server_paths(api_settings: Settings) -> None:
    with _client(api_settings) as client:
        files = client.get("/raw-files", headers=KEY).json()["files"]
    assert files and all(f["raw_path"] == f["name"] for f in files)


def test_listing_a_missing_raw_dir_creates_nothing(api_settings: Settings, tmp_path: Path) -> None:
    missing = tmp_path / "absent"
    settings = api_settings.model_copy(update={"RAW_DATA_DIR": missing})
    with _client(settings) as client:
        assert client.get("/raw-files", headers=KEY).json()["total"] == 0
    assert not missing.exists()


def test_symlink_escaping_raw_dir_is_not_listed_or_runnable(
    api_settings: Settings, raw_dir: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.csv"
    outside.write_text("customer_id\n1\n", encoding="utf-8")
    try:
        (raw_dir / "link.csv").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not permitted here")
    with _client(api_settings) as client:
        names = [f["name"] for f in client.get("/raw-files", headers=KEY).json()["files"]]
        run = client.post("/runs", json={"raw_path": "link.csv"}, headers=KEY)
    assert "link.csv" not in names
    assert run.status_code == 400


# --- N-M15: concurrent same-name uploads never overwrite ---------------------------


def test_concurrent_same_name_uploads_never_overwrite(tmp_path: Path) -> None:
    from api.routes.uploads import _publish_new

    target = tmp_path / "data.csv"
    temps = []
    for i in range(6):
        temp = tmp_path / f".data.csv.{i}.part"
        temp.write_text(f"content {i}", encoding="utf-8")
        temps.append(temp)
    contents = {t.read_text(encoding="utf-8") for t in temps}
    barrier = threading.Barrier(len(temps))
    created: list[bool] = []

    def publish(temp: Path) -> None:
        barrier.wait()
        created.append(_publish_new(temp, target))

    threads = [threading.Thread(target=publish, args=(t,)) for t in temps]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert created.count(True) == 1
    assert target.read_text(encoding="utf-8") in contents


# --- T3: a 409 confirm writes no second mapping file ---------------------------------


def test_409_confirm_writes_no_second_file(
    client: TestClient, auth_headers: dict[str, str], raw_dir: Path, api_settings: Settings
) -> None:
    from tests.api.test_mapping_endpoints import mapping_payload_from

    raw_path = str(raw_dir / "unmapped_export.csv")
    fingerprint = client.post(
        "/mappings/draft", json={"raw_path": raw_path}, headers=auth_headers
    ).json()["source_fingerprint"]
    body = {"report": mapping_payload_from(fingerprint), "raw_path": raw_path}
    mappings = Path(api_settings.CONFIG_DIR) / "mappings"
    before = set(mappings.glob("map_*.json"))
    assert client.post("/mappings/confirm", json=body, headers=auth_headers).status_code == 200
    after_first = set(mappings.glob("map_*.json"))
    assert client.post("/mappings/confirm", json=body, headers=auth_headers).status_code == 409
    assert set(mappings.glob("map_*.json")) == after_first
    assert len(after_first - before) == 1

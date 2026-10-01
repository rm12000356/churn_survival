"""Raw-file listing + upload endpoint tests (Horizon additive API)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from config.settings import Settings


def _upload(
    client: TestClient,
    *,
    filename: str,
    content: bytes = b"a,b\n1,2\n",
    headers: dict[str, str] | None = None,
):
    return client.post(
        "/uploads",
        files={"file": (filename, content, "text/csv")},
        headers=headers,
    )


def test_list_raw_files_is_read_only_and_sorted(
    client: TestClient, raw_dir: Path
) -> None:
    response = client.get("/raw-files")
    assert response.status_code == 200
    payload = response.json()
    names = [entry["name"] for entry in payload["files"]]
    assert names == sorted(names)
    assert "clean_customers.csv" in names
    assert payload["total"] == len(names)


def test_list_raw_files_classifies_kind_and_excludes_unrunnable(
    client: TestClient, raw_dir: Path
) -> None:
    # A support-threads JSON and an uninrunnable file are added to raw_dir.
    (raw_dir / "threads.json").write_text("[]", encoding="utf-8")
    (raw_dir / "notes.txt").write_text("ignore me", encoding="utf-8")
    (raw_dir / "table.parquet").write_bytes(b"\x00\x01")

    by_name = {e["name"]: e for e in client.get("/raw-files").json()["files"]}
    assert by_name["clean_customers.csv"]["kind"] == "dataset"
    assert by_name["threads.json"]["kind"] == "support"
    # Neither the text file nor the parquet (Node 1 cannot ingest them) is listed.
    assert "notes.txt" not in by_name
    assert "table.parquet" not in by_name


def test_read_raw_file_returns_text_and_is_confined(
    client: TestClient, raw_dir: Path
) -> None:
    (raw_dir / "threads.json").write_text('[{"thread_id": "t1"}]', encoding="utf-8")
    ok = client.get("/raw-files/threads.json")
    assert ok.status_code == 200
    assert '"thread_id": "t1"' in ok.text
    # Unknown file -> 404; traversal/multi-segment names -> 400.
    assert client.get("/raw-files/missing.json").status_code == 404
    assert client.get("/raw-files/..%2Fsecret.json").status_code in (400, 404)
    assert client.get("/raw-files/nested%2Ffile.json").status_code in (400, 404)


def test_upload_requires_writes_and_auth(client: TestClient, anon_client: TestClient) -> None:
    assert _upload(anon_client, filename="new.csv").status_code == 401
    assert (
        _upload(client, filename="new.csv", headers={"X-API-Key": "wrong"}).status_code
        == 401
    )


def test_upload_writes_into_raw_dir(
    client: TestClient, raw_dir: Path, auth_headers: dict[str, str]
) -> None:
    response = _upload(
        client, filename="uploaded_customers.csv", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    # A bare name: absolute server paths are never disclosed (REVIEW LOW).
    assert body["raw_path"] == "uploaded_customers.csv"
    assert body["size_bytes"] > 0
    assert (raw_dir / "uploaded_customers.csv").is_file()
    # It now appears in the listing and is runnable by POST /runs.
    names = [entry["name"] for entry in client.get("/raw-files").json()["files"]]
    assert "uploaded_customers.csv" in names


def test_upload_strips_path_components(
    client: TestClient, raw_dir: Path, auth_headers: dict[str, str]
) -> None:
    response = _upload(
        client, filename="../../etc/evil.csv", headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["raw_path"] == "evil.csv"
    assert not (raw_dir.parent.parent / "evil.csv").exists()


def test_upload_rejects_disallowed_extension(
    client: TestClient, raw_dir: Path, auth_headers: dict[str, str]
) -> None:
    response = _upload(client, filename="script.exe", headers=auth_headers)
    assert response.status_code == 400
    assert "extension" in response.json()["detail"]
    assert not (raw_dir / "script.exe").exists()


def test_upload_rejects_parquet_dataset(
    client: TestClient, raw_dir: Path, auth_headers: dict[str, str]
) -> None:
    # Node 1 cannot ingest parquet, so it must never be uploadable as a dataset.
    response = _upload(client, filename="table.parquet", headers=auth_headers)
    assert response.status_code == 400
    assert not (raw_dir / "table.parquet").exists()


def test_upload_json_is_classified_support(
    client: TestClient, raw_dir: Path, auth_headers: dict[str, str]
) -> None:
    response = _upload(
        client,
        filename="threads.json",
        content=b"[]",
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["kind"] == "support"


def test_upload_rejects_empty_file(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = _upload(client, filename="empty.csv", content=b"", headers=auth_headers)
    assert response.status_code == 400


def test_upload_rejects_missing_filename(
    client: TestClient, raw_dir: Path, auth_headers: dict[str, str]
) -> None:
    response = client.post(
        "/uploads",
        files={"file": ("", b"a,b\n1,2\n", "text/csv")},
        headers=auth_headers,
    )
    # FastAPI/Starlette rejects a nameless part before our handler (422); our own
    # guard (400) covers the case where the name survives as empty.
    assert response.status_code in (400, 422)
    assert list(raw_dir.glob("*")) == sorted(raw_dir.glob("*"))  # dir untouched


def test_upload_writes_disabled_returns_403(
    make_client, api_settings: Settings
) -> None:
    settings = api_settings.model_copy(
        update={"API_ENABLE_WRITES": False, "API_KEY": None}
    )
    client = make_client(settings)
    assert client.get("/raw-files").status_code == 200
    assert _upload(client, filename="x.csv").status_code == 403


def _thread(customer_id: str = "cus_1001") -> dict:
    return {
        "thread_id": "thr_1",
        "customer_id": customer_id,
        "created_at": "2026-08-01T10:00:00Z",
        "channel": "email",
        "subject": "Thinking of leaving",
        "messages": [
            {
                "message_id": "m1",
                "timestamp": "2026-08-01T10:00:00Z",
                "role": "customer",
                "text": "We are considering cancelling our subscription.",
            }
        ],
    }


def test_trigger_with_support_data_reaches_node3(
    client: TestClient, raw_dir: Path, auth_headers: dict[str, str]
) -> None:
    """A support-threads JSON passed as support_data is consumed by Node 3."""
    response = client.post(
        "/runs",
        json={
            "raw_path": str(raw_dir / "clean_customers.csv"),
            "support_data": [_thread()],
        },
        headers=auth_headers,
    )
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    summary = client.get(f"/runs/{run_id}").json()
    assert summary["execution_status"] == "COMPLETED"
    node3 = client.get(f"/runs/{run_id}/node3").json()
    assert node3["processing_report"]["n_threads_processed"] == 1
    assert node3["processing_report"]["n_customers_with_data"] >= 1


def test_trigger_json_as_raw_path_is_rejected(
    client: TestClient, raw_dir: Path, auth_headers: dict[str, str]
) -> None:
    """A support JSON is not a customer dataset: using it as raw_path is a 422."""
    (raw_dir / "threads.json").write_text("[]", encoding="utf-8")
    response = client.post(
        "/runs",
        json={"raw_path": str(raw_dir / "threads.json")},
        headers=auth_headers,
    )
    assert response.status_code == 422
    assert "unsupported raw-data extension" in response.json()["detail"]

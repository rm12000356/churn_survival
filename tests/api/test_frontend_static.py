"""Frontend static mount tests (Horizon additive serving).

The static frontend is mounted at ``/`` only when ``FRONTEND_DIR`` exists; the
explicit API routers keep precedence. These tests use the repo's real
``frontend/`` directory and never recompute anything.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from config.settings import Settings

REPO = Path(__file__).resolve().parents[2]


def test_static_frontend_mounts_without_shadowing_api(
    make_client, api_settings: Settings
) -> None:
    settings = api_settings.model_copy(
        update={"FRONTEND_DIR": REPO / "frontend"}
    )
    client = make_client(settings)
    # API routes still win, even though "/" is mounted after them.
    assert client.get("/health").status_code == 200
    assert client.get("/raw-files", headers={"X-API-Key": "secret-key"}).status_code == 200
    # The static UI needs no key (it asks the user for one).
    index = client.get("/")
    assert index.status_code == 200
    assert "text/html" in index.headers["content-type"]
    assert "Horizon" in index.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/views/report.js").status_code == 200


def test_static_frontend_is_revalidated_not_stale(
    make_client, api_settings: Settings
) -> None:
    """A frontend update must reach browsers: JS modules may not be heuristically
    cached (a stale history.js kept the old run filter after it was fixed)."""
    settings = api_settings.model_copy(update={"FRONTEND_DIR": REPO / "frontend"})
    client = make_client(settings)
    for path in ("/", "/static/app.js", "/static/views/history.js", "/static/app.css"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-cache"
    # API responses are not affected by the frontend mount.
    assert "no-cache" not in client.get("/health").headers.get("cache-control", "")


def test_absent_frontend_dir_leaves_api_only(
    make_client, api_settings: Settings, tmp_path: Path
) -> None:
    settings = api_settings.model_copy(
        update={"FRONTEND_DIR": tmp_path / "does-not-exist"}
    )
    client = make_client(settings)
    assert client.get("/health").status_code == 200
    assert client.get("/").status_code == 404


def test_upload_endpoints_registered(client: TestClient) -> None:
    # Sanity: the additive routers are wired into the app.
    assert client.get("/raw-files").status_code == 200


def test_frontend_wires_run_id_from_path_segments() -> None:
    """Regression: views must read the run id from the path, set as params.id.

    The id bug came from views reading ctx.params.id while the router only put
    path segments in ctx.segments. app.js now centralizes it as params.id.
    """
    app_js = (REPO / "frontend" / "static" / "app.js").read_text(encoding="utf-8")
    assert "ctx.params.id = parsed.segments[1]" in app_js
    for view in ("runStatus.js", "report.js", "mapping.js"):
        text = (REPO / "frontend" / "static" / "views" / view).read_text(encoding="utf-8")
        assert "ctx.params.id" in text


def test_frontend_has_support_threads_picker() -> None:
    upload_js = (REPO / "frontend" / "static" / "views" / "upload.js").read_text(
        encoding="utf-8"
    )
    assert "support-select" in upload_js
    assert "support_data" in upload_js
    assert "loadSupportData" in upload_js
    picker_js = (REPO / "frontend" / "static" / "components" / "filePicker.js").read_text(
        encoding="utf-8"
    )
    assert "readRawFile" in picker_js
    api_js = (REPO / "frontend" / "static" / "api.js").read_text(encoding="utf-8")
    assert "readRawFile" in api_js


def test_every_input_section_has_its_own_file_upload() -> None:
    """Dataset (Node 1), support threads (Node 3) and mapping each get their own input."""
    views = REPO / "frontend" / "static" / "views"
    upload_js = (views / "upload.js").read_text(encoding="utf-8")
    assert 'kind: "dataset"' in upload_js
    assert 'kind: "support"' in upload_js
    assert "mapping-file" in upload_js
    assert "confirmMapping" in upload_js
    picker_js = (REPO / "frontend" / "static" / "components" / "filePicker.js").read_text(
        encoding="utf-8"
    )
    assert 'type: "file"' in picker_js
    assert "api.upload" in picker_js


def test_mapping_screen_offers_manual_llm_and_file_upload() -> None:
    """A stopped run can be mapped by hand, by the LLM, or from the user's own file."""
    mapping_js = (REPO / "frontend" / "static" / "views" / "mapping.js").read_text(
        encoding="utf-8"
    )
    assert "Map it myself" in mapping_js
    assert "draftMapping(rawPath, true)" in mapping_js
    assert "readMappingFile" in mapping_js
    # The re-triggered run can carry support threads again (Node 3 input).
    assert "support_data" in mapping_js
    editor_js = (REPO / "frontend" / "static" / "components" / "mappingEditor.js").read_text(
        encoding="utf-8"
    )
    # Manual mode must offer a row per dataset column (the draft has no rows).
    assert "column_names" in editor_js


def test_api_parses_by_content_type_not_blindly() -> None:
    """Regression: the fetch wrapper must not blind-JSON-parse every response.

    A JSON body served as ``text/plain`` (support threads) must be returned as a
    string so the caller can parse it; otherwise ``JSON.parse`` double-parses.
    """
    api_js = (REPO / "frontend" / "static" / "api.js").read_text(encoding="utf-8")
    assert 'response.headers.get("content-type")' in api_js
    assert api_js.count("application/json") >= 2  # request Content-Type + response check
    assert "getReportHtml" in api_js
    assert "reportHtmlUrl" in api_js


def test_report_view_links_static_report() -> None:
    report_js = (REPO / "frontend" / "static" / "views" / "report.js").read_text(
        encoding="utf-8"
    )
    assert "reportHtmlUrl" in report_js
    assert "Open static report" in report_js


def test_frontend_wires_node1_config_override() -> None:
    """The upload view offers an explicit Node 1 deployment-config override."""
    upload_js = (REPO / "frontend" / "static" / "views" / "upload.js").read_text(
        encoding="utf-8"
    )
    assert "node1-select" in upload_js
    assert "listNode1Configs" in upload_js
    assert "node1_version" in upload_js
    api_js = (REPO / "frontend" / "static" / "api.js").read_text(encoding="utf-8")
    assert "listNode1Configs" in api_js
    mapping_js = (REPO / "frontend" / "static" / "views" / "mapping.js").read_text(
        encoding="utf-8"
    )
    assert "node1_config_version" in mapping_js


def test_node1_configs_endpoint_registered(client: TestClient) -> None:
    assert client.get("/node1-configs").status_code == 200

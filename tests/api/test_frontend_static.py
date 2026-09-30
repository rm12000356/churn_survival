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
    assert client.get("/raw-files").status_code == 200
    index = client.get("/")
    assert index.status_code == 200
    assert "text/html" in index.headers["content-type"]
    assert "Horizon" in index.text
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/views/report.js").status_code == 200


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
    assert "readRawFile" in upload_js
    api_js = (REPO / "frontend" / "static" / "api.js").read_text(encoding="utf-8")
    assert "readRawFile" in api_js


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

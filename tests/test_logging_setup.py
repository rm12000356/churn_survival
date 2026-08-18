from __future__ import annotations

import json

import pytest


def test_structured_log_line_has_required_fields(
    fresh_settings: None, capsys: pytest.CaptureFixture[str]
) -> None:
    from logging_setup import configure_logging, get_logger

    configure_logging(force=True)
    get_logger(node="node1", mapping_version="map_v1", model_version="v3").info(
        "node_started", n_rows=42
    )

    out = capsys.readouterr().out
    line = json.loads(out.strip().splitlines()[-1])

    assert line["event"] == "node_started"
    assert line["level"] == "info"
    assert line["node"] == "node1"
    assert line["mapping_version"] == "map_v1"
    assert line["model_version"] == "v3"
    assert line["n_rows"] == 42
    assert "timestamp" in line


def test_logging_respects_level(fresh_settings: None, capsys: pytest.CaptureFixture[str]) -> None:
    from logging_setup import configure_logging, get_logger

    configure_logging(force=True)
    get_logger(node="node2").debug("hidden")  # default LOG_LEVEL is INFO
    assert capsys.readouterr().out == ""

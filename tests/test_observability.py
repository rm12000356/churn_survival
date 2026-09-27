"""Structured observability tests (ROADMAP Task 9.1, architecture §8.8).

Verifies that a full run emits machine-parseable events carrying the node and
its version fields, and that no secret or untrusted content is ever logged
(D-H4). Logs are written to stderr so command stdout stays deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestration.graph import run_pipeline

FIXTURES = Path(__file__).resolve().parent / "adapters" / "fixtures"


@pytest.fixture
def clean_csv() -> Path:
    return FIXTURES / "clean_customers.csv"


def _events(stderr: str) -> list[dict]:
    parsed = []
    for line in stderr.splitlines():
        line = line.strip()
        if line.startswith("{"):
            parsed.append(json.loads(line))
    return parsed


def test_run_pipeline_emits_structured_events(
    fresh_settings: None, capsys: pytest.CaptureFixture[str], clean_csv: Path
) -> None:
    from logging_setup import configure_logging

    configure_logging(force=True)
    result = run_pipeline(clean_csv)

    events = _events(capsys.readouterr().err)
    by_event: dict[str, list[dict]] = {}
    for event in events:
        by_event.setdefault(event["event"], []).append(event)

    assert "run_started" in by_event
    assert "run_completed" in by_event

    stages = by_event["stage_finished"]
    nodes = {stage["node"] for stage in stages}
    assert {"node1", "node2", "node3", "node4", "node5"} <= nodes
    for stage in stages:
        assert stage["config_version"] is not None
        assert "timestamp" in stage

    node2 = next(stage for stage in stages if stage["node"] == "node2")
    assert "model_version" in node2 and node2["model_version"]
    node1 = next(stage for stage in stages if stage["node"] == "node1")
    assert "n_accepted" in node1

    completed = by_event["run_completed"][-1]
    assert completed["run_id"] == result.state.run_id
    assert completed["status"] == "COMPLETED"

    for event in events:
        assert event["level"] in {"info", "warning", "error", "critical", "debug"}
        assert "timestamp" in event


def test_logs_do_not_leak_secrets_or_message_text(
    fresh_settings: None,
    capsys: pytest.CaptureFixture[str],
    clean_csv: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from logging_setup import configure_logging

    secret = "SUPER-SECRET-API-KEY-123"
    message_text = "SENTINEL-MESSAGE-TEXT-do-not-log"
    monkeypatch.setenv("API_KEY", secret)
    # Force settings to re-read the environment after the monkeypatch.
    import config.settings as cs

    monkeypatch.setattr(cs, "_settings", None)
    configure_logging(force=True)

    support_data = [
        {
            "thread_id": "thr_secret",
            "customer_id": "cus_secret",
            "created_at": "2026-07-20T14:30:00Z",
            "closed_at": None,
            "channel": "email",
            "subject": "billing",
            "messages": [
                {
                    "message_id": "msg_secret",
                    "timestamp": "2026-07-20T14:30:00Z",
                    "role": "customer",
                    "text": message_text,
                }
            ],
        }
    ]
    run_pipeline(clean_csv, support_data=support_data)

    stderr = capsys.readouterr().err
    assert secret not in stderr
    assert message_text not in stderr


def test_emit_node_completion_writes_json_to_stderr(
    fresh_settings: None, capsys: pytest.CaptureFixture[str]
) -> None:
    from logging_setup import emit_node_completion

    emit_node_completion("node1", config_version="1", n_accepted=7)

    captured = capsys.readouterr()
    assert captured.out == ""
    line = json.loads(captured.err.strip().splitlines()[-1])
    assert line["event"] == "node_run_completed"
    assert line["node"] == "node1"
    assert line["n_accepted"] == 7

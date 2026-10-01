"""CLI tests for ``churn-survival run`` (ROADMAP Phase 7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestration.node import main as run_main
from pipeline.main import main


def test_run_usage_without_file() -> None:
    assert run_main([]) == 2


def test_run_dispatch_from_pipeline_main() -> None:
    assert main(["run"]) == 2


def test_run_clean_file_succeeds(clean_csv: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run_main([str(clean_csv)]) == 0
    out = capsys.readouterr().out
    assert "status=COMPLETED" in out


def test_confirm_mapping_requires_mapping_file(unmapped_csv: Path) -> None:
    assert run_main([str(unmapped_csv), "--confirm-mapping"]) == 2


def test_run_writes_result_json(clean_csv: Path, tmp_path: Path) -> None:
    out = tmp_path / "result.json"
    assert run_main([str(clean_csv), "--output", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["status"] == "COMPLETED"
    assert payload["node5_output"] is not None


def test_run_missing_file_fails_loudly(tmp_path: Path) -> None:
    assert run_main([str(tmp_path / "does_not_exist.csv")]) == 1


def test_run_with_threads_file(clean_csv: Path, tmp_path: Path) -> None:
    threads_path = tmp_path / "threads.json"
    threads_path.write_text(
        json.dumps(
            [
                {
                    "thread_id": "thr_cli_1",
                    "customer_id": "cus_1001",
                    "created_at": "2026-07-20T14:30:00Z",
                    "messages": [
                        {
                            "message_id": "msg_cli_1",
                            "timestamp": "2026-07-20T14:30:00Z",
                            "role": "customer",
                            "text": "We are considering cancelling before the renewal.",
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    assert run_main([str(clean_csv), "--threads", str(threads_path)]) == 0


def test_run_confirms_mapping_via_cli(
    monkeypatch: pytest.MonkeyPatch, unmapped_csv: Path, tmp_path: Path
) -> None:
    import shutil

    import pandas as pd

    import config.settings as cs
    from router.fingerprint import extract_fingerprint
    from schemas.mapping import MappingReport
    from tests.mapping_helpers import mapping_payload

    repo = Path(__file__).resolve().parents[2]
    config_copy = tmp_path / "config"
    shutil.copytree(repo / "config", config_copy)
    monkeypatch.setenv("CONFIG_DIR", str(config_copy))
    monkeypatch.setattr(cs, "_settings", None)

    report = MappingReport.model_validate(
        mapping_payload(extract_fingerprint(pd.read_csv(unmapped_csv)))
    )
    report_path = tmp_path / "report.json"
    report_path.write_text(report.model_dump_json(), encoding="utf-8")

    code = run_main([str(unmapped_csv), "--mapping", str(report_path), "--confirm-mapping"])
    assert code == 0
    assert list((config_copy / "mappings").glob("map_*.json"))


def test_run_flag_missing_value_is_usage_error() -> None:
    assert run_main(["--node1"]) == 2


def test_run_threads_file_must_be_a_list(clean_csv: Path, tmp_path: Path) -> None:
    bad = tmp_path / "threads.json"
    bad.write_text("{}", encoding="utf-8")
    assert run_main([str(clean_csv), "--threads", str(bad)]) == 1


def test_run_mock_dir_flag(clean_csv: Path, tmp_path: Path) -> None:
    assert run_main([str(clean_csv), "--mock-dir", str(tmp_path)]) == 0


def test_run_sources_mock(clean_csv: Path) -> None:
    assert run_main([str(clean_csv), "--sources", "mock"]) == 0


def test_run_unmapped_returns_one(
    unmapped_csv: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_main([str(unmapped_csv)]) == 1
    assert "unknown" in capsys.readouterr().err.lower()



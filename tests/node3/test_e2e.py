from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.models import Node3Config, VocabularyConfig
from node3.node import main as node3_main
from node3.node import run_node3
from pipeline.main import run_node
from tests.node3.conftest import NOW, message, thread


def test_end_to_end_multi_customer(
    node3_config: Node3Config, vocabulary: VocabularyConfig
) -> None:
    threads = [
        thread(
            "T1",
            "CUST-1",
            created_at="2026-08-01T00:00:00Z",
            messages=[message("m1", "I want to cancel my subscription.")],
        ),
        thread(
            "T2",
            "CUST-2",
            created_at="2026-08-01T00:00:00Z",
            messages=[message("m2", "Love the product, great job!")],
        ),
        thread(
            "T3",
            "CUST-3",
            created_at="2026-08-01T00:00:00Z",
            messages=[message("m3", "Can you reset my password?")],
        ),
    ]
    output = run_node3(
        ["CUST-1", "CUST-2", "CUST-3", "CUST-4"],
        threads,
        node3_config,
        vocabulary=vocabulary,
        now=NOW,
    )
    report = output.processing_report
    assert report.n_customers_requested == 4
    assert report.n_customers_with_data == 3
    assert report.n_threads_processed == 3
    assert output.customer_signals[3].support_data_status.value == "no_data"


def test_cli_writes_output(
    fresh_settings: None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    threads_path = tmp_path / "threads.json"
    threads_path.write_text(json.dumps([thread("T1", "CUST-1")]), encoding="utf-8")
    out_path = tmp_path / "out.json"
    code = node3_main([str(threads_path), "--config", "1", "--output", str(out_path)])
    assert code == 0
    assert out_path.is_file()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["processing_report"]["n_threads_processed"] == 1
    assert "Node 3:" in capsys.readouterr().out


def test_cli_accepts_customers_csv(
    fresh_settings: None, tmp_path: Path
) -> None:
    threads_path = tmp_path / "threads.json"
    threads_path.write_text(json.dumps([thread("T1", "CUST-1")]), encoding="utf-8")
    customers_path = tmp_path / "customers.csv"
    customers_path.write_text("customer_id\nCUST-1\nCUST-2\n", encoding="utf-8")
    code = node3_main(
        [str(threads_path), "--customers", str(customers_path), "--config", "1"]
    )
    assert code == 0


def test_cli_bad_args_returns_usage(fresh_settings: None) -> None:
    assert node3_main([]) == 2


def test_cli_missing_file_fails_loudly(
    fresh_settings: None, capsys: pytest.CaptureFixture[str]
) -> None:
    assert node3_main(["does-not-exist.json", "--config", "1"]) == 1
    assert "ERROR" in capsys.readouterr().err


def test_pipeline_dispatch(fresh_settings: None, tmp_path: Path) -> None:
    threads_path = tmp_path / "threads.json"
    threads_path.write_text(json.dumps([thread("T1", "CUST-1")]), encoding="utf-8")
    assert run_node("node3", [str(threads_path), "--config", "1"]) == 0


def test_cli_accepts_customers_json(fresh_settings: None, tmp_path: Path) -> None:
    threads_path = tmp_path / "threads.json"
    threads_path.write_text(json.dumps([thread("T1", "CUST-1")]), encoding="utf-8")
    customers_path = tmp_path / "customers.json"
    customers_path.write_text(json.dumps(["CUST-1", "CUST-2"]), encoding="utf-8")
    assert (
        node3_main([str(threads_path), "--customers", str(customers_path), "--config", "1"])
        == 0
    )


def test_create_llm_client_when_provider_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REFERENCE_DATE", "2026-08-15")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_MODEL", "gpt-test")
    import config.settings as cs

    monkeypatch.setattr(cs, "_settings", None)
    from node3.node import _create_llm_client_or_none

    client = _create_llm_client_or_none()
    assert client is not None
    assert client.model == "gpt-test"


def test_create_llm_client_none_without_provider(fresh_settings: None) -> None:
    from node3.node import _create_llm_client_or_none

    assert _create_llm_client_or_none() is None

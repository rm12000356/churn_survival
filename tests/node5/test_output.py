"""Milestone A — output contract, CLI, and determinism (architecture §5.26/§5.27)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from node5.node import main as node5_main
from node5.node import run_node5
from node5.rendering.json import render_json
from schemas.node5 import Node5Output
from tests.node5.conftest import make_sample_inputs, make_sample_node4


def test_run_node5_produces_full_contract(node5_config, action_rules) -> None:
    node4, node3 = make_sample_inputs()
    output = run_node5(node4, node5_config, node3_output=node3, action_rules=action_rules)
    assert isinstance(output, Node5Output)
    assert output.report.title == "Customer Risk Report"
    assert output.report.reference_date == node4.reference_date
    stats = node4.summary_stats
    distribution = output.report.risk_distribution
    assert distribution.critical == stats.n_critical
    assert distribution.high == stats.n_high
    assert distribution.medium == stats.n_medium
    assert distribution.low == stats.n_low
    assert distribution.insufficient_data == stats.n_insufficient_data
    assert output.processing_report.n_accounts == stats.n_customers


def test_priority_accounts_are_node4_prefix_in_order(node5_config, action_rules) -> None:
    node4, node3 = make_sample_inputs()
    output = run_node5(node4, node5_config, node3_output=node3, action_rules=action_rules)
    assert [a.customer_id for a in output.report.priority_accounts] == [
        a.customer_id for a in node4.ranked_accounts[: len(output.report.priority_accounts)]
    ]


def test_decision_fields_are_copied_verbatim(node5_config, action_rules) -> None:
    node4, node3 = make_sample_inputs()
    output = run_node5(node4, node5_config, node3_output=node3, action_rules=action_rules)
    by_id = {a.customer_id: a for a in node4.ranked_accounts}
    for report in output.report.priority_accounts:
        source = by_id[report.customer_id]
        assert report.rank == source.rank
        assert report.risk_level.value == source.combined_risk_level.value
        assert report.combined_score == source.combined_score
        assert report.combined_confidence == source.combined_confidence


def test_insufficient_data_is_separate_and_unranked(node5_config, action_rules) -> None:
    node4, node3 = make_sample_inputs()
    output = run_node5(node4, node5_config, node3_output=node3, action_rules=action_rules)
    assert node4.insufficient_data_accounts
    main_ids = {a.customer_id for a in output.report.priority_accounts}
    insufficient_ids = {a.customer_id for a in output.report.insufficient_data_accounts}
    assert main_ids.isdisjoint(insufficient_ids)
    for report in output.report.insufficient_data_accounts:
        assert report.rank is None


def test_deterministic_rerun_is_bit_identical(node5_config, action_rules) -> None:
    node4, node3 = make_sample_inputs()
    first = run_node5(node4, node5_config, node3_output=node3, action_rules=action_rules)
    second = run_node5(node4, node5_config, node3_output=node3, action_rules=action_rules)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_generated_at_is_derived_not_wall_clock(node5_config, action_rules) -> None:
    node4, node3 = make_sample_inputs()
    output = run_node5(node4, node5_config, node3_output=node3, action_rules=action_rules)
    assert output.metadata.generated_at.isoformat().startswith("2026-08-15T00:00:00")


def test_json_renderer_is_deterministic(node5_config, action_rules) -> None:
    node4, node3 = make_sample_inputs()
    output = run_node5(node4, node5_config, node3_output=node3, action_rules=action_rules)
    assert render_json(output) == render_json(output)
    metadata = json.loads(render_json(output))["metadata"]
    assert metadata["report_version"] == node5_config.report_version


def test_cli_writes_output(
    fresh_settings: None, tmp_path: Path, node5_config, capsys: pytest.CaptureFixture[str]
) -> None:
    node4 = make_sample_node4()
    node4_path = tmp_path / "node4.json"
    node4_path.write_text(
        json.dumps(node4.model_dump(mode="json")), encoding="utf-8"
    )
    out_path = tmp_path / "node5.json"
    code = node5_main(
        [
            "--node4",
            str(node4_path),
            "--config",
            "1",
            "--action-rules",
            "1",
            "--output",
            str(out_path),
        ]
    )
    assert code == 0
    assert out_path.is_file()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["metadata"]["report_version"] == node5_config.report_version
    assert "Node 5:" in capsys.readouterr().out


def test_cli_bad_args_returns_usage(fresh_settings: None) -> None:
    assert node5_main([]) == 2


def test_cli_missing_file_fails_loudly(fresh_settings: None) -> None:
    assert node5_main(["--node4", "does-not-exist.json", "--config", "1"]) == 1

"""Milestone A/B — config, provenance, and evidence failure modes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from node5.node import main as node5_main
from node5.node import run_node5
from node5.report.consistency import DoNotPublishError
from tests.node5.conftest import make_sample_node4


def test_mixed_provenance_blocks_publication(node5_config, action_rules) -> None:
    node4 = make_sample_node4()
    account = node4.ranked_accounts[0]
    node3_ref = account.evidence_refs.node3.model_copy(update={"signal_version": "other"})
    mutated_account = account.model_copy(
        update={"evidence_refs": account.evidence_refs.model_copy(update={"node3": node3_ref})}
    )
    mutated = node4.model_copy(
        update={"ranked_accounts": [mutated_account, *node4.ranked_accounts[1:]]}
    )
    with pytest.raises(DoNotPublishError) as excinfo:
        run_node5(mutated, node5_config, action_rules=action_rules)
    assert any("mixed provenance" in failure for failure in excinfo.value.failures)


def test_truncation_records_warning(node5_config, action_rules) -> None:
    config = node5_config.model_copy(update={"max_accounts_in_summary": 1})
    node4 = make_sample_node4()
    output = run_node5(node4, config, action_rules=action_rules)
    assert len(output.report.priority_accounts) == 1
    assert any("truncated" in warning for warning in output.processing_report.warnings)
    # Portfolio distribution is unchanged by the prefix cap.
    assert output.report.risk_distribution.critical == node4.summary_stats.n_critical


def test_missing_action_rules_blocks_publication(node5_config) -> None:
    # Recommendations are enabled by default, so a missing action_rules_version is
    # a required-provenance failure (F-3): the report must not be published.
    with pytest.raises(DoNotPublishError):
        run_node5(make_sample_node4(), node5_config, action_rules=None)


def test_missing_action_rules_ok_when_recommendations_disabled(node5_config) -> None:
    config = node5_config.model_copy(update={"include_recommendations": False})
    output = run_node5(make_sample_node4(), config, action_rules=None)
    assert all(a.recommended_action is None for a in output.report.priority_accounts)
    assert output.metadata.action_rules_version is None


def test_recommendations_can_be_disabled(node5_config, action_rules) -> None:
    config = node5_config.model_copy(update={"include_recommendations": False})
    output = run_node5(make_sample_node4(), config, action_rules=action_rules)
    assert all(a.recommended_action is None for a in output.report.priority_accounts)


def test_insufficient_section_can_be_omitted(node5_config, action_rules) -> None:
    config = node5_config.model_copy(update={"include_insufficient_data": False})
    node4 = make_sample_node4()
    output = run_node5(node4, config, action_rules=action_rules)
    assert output.report.insufficient_data_accounts == []
    # Counts remain authoritative from Node 4.
    assert (
        output.report.risk_distribution.insufficient_data
        == node4.summary_stats.n_insufficient_data
    )
    assert output.processing_report.n_insufficient_data == node4.summary_stats.n_insufficient_data


def test_language_other_than_english_warns(node5_config, action_rules) -> None:
    config = node5_config.model_copy(update={"language": "fr"})
    output = run_node5(make_sample_node4(), config, action_rules=action_rules)
    assert any("English" in warning for warning in output.processing_report.warnings)


def test_malformed_top_flags_are_reported_not_dropped(node5_config, action_rules) -> None:
    node4 = make_sample_node4()
    base = node4.ranked_accounts[0]
    account = base.model_copy(
        update={
            "qualitative": base.qualitative.model_copy(
                update={"top_flags": [None, "bad", {"flag_type": "billing_complaint"}]}
            )
        }
    )
    mutated = node4.model_copy(
        update={"ranked_accounts": [account, *node4.ranked_accounts[1:]]}
    )
    output = run_node5(mutated, node5_config, action_rules=action_rules)
    invalid = [
        error
        for error in output.processing_report.errors
        if error["code"] == "INVALID_TOP_FLAG"
    ]
    assert len(invalid) == 3
    assert output.report.priority_accounts  # report still generated


def test_missing_node3_yields_reference_only_evidence(node5_config, action_rules) -> None:
    output = run_node5(make_sample_node4(), node5_config, action_rules=action_rules)
    for account in output.report.priority_accounts:
        assert all(item.node3_reference is None for item in account.evidence)


def test_cli_writes_html(fresh_settings: None, tmp_path: Path, capsys) -> None:
    node4 = make_sample_node4()
    node4_path = tmp_path / "node4.json"
    node4_path.write_text(json.dumps(node4.model_dump(mode="json")), encoding="utf-8")
    html_path = tmp_path / "report.html"
    code = node5_main(
        [
            "--node4",
            str(node4_path),
            "--config",
            "1",
            "--action-rules",
            "1",
            "--html",
            str(html_path),
        ]
    )
    assert code == 0
    assert html_path.is_file()
    assert "Customer Risk Report" in html_path.read_text(encoding="utf-8")
    assert "HTML written" in capsys.readouterr().out

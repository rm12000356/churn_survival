"""Milestone A — critical invariants that hold without any LLM (§5.34)."""

from __future__ import annotations

from node5.node import run_node5
from node5.report.consistency import check_consistency
from tests.node5.conftest import make_sample_inputs, make_sample_node4


def _run(config, action_rules, **kwargs):
    node4, node3 = make_sample_inputs()
    return node4, run_node5(node4, config, node3_output=node3, action_rules=action_rules, **kwargs)


def test_risk_levels_are_not_changed(node5_config, action_rules) -> None:
    node4, output = _run(node5_config, action_rules)
    by_id = {a.customer_id: a for a in node4.ranked_accounts}
    for report in output.report.priority_accounts:
        assert report.risk_level.value == by_id[report.customer_id].combined_risk_level.value


def test_ranks_scores_confidence_are_not_changed(node5_config, action_rules) -> None:
    node4, output = _run(node5_config, action_rules)
    by_id = {a.customer_id: a for a in node4.ranked_accounts}
    for report in output.report.priority_accounts:
        source = by_id[report.customer_id]
        assert report.rank == source.rank
        assert report.combined_score == source.combined_score
        assert report.combined_confidence == source.combined_confidence


def test_removing_llm_still_produces_a_valid_report(node5_config, action_rules) -> None:
    node4 = make_sample_node4()
    output = run_node5(node4, node5_config, action_rules=action_rules, llm_client=None)
    assert output.processing_report.llm_calls == 0
    assert output.report.priority_accounts
    assert all(account.summary for account in output.report.priority_accounts)


def test_every_critical_keeps_its_critical_reason(node5_config, action_rules) -> None:
    _, output = _run(node5_config, action_rules)
    for report in output.report.priority_accounts:
        if report.risk_level.value == "critical":
            assert any(
                reason.reason_type.value.startswith("critical_")
                for reason in report.primary_reasons
            )


def test_statistics_match_node4(node5_config, action_rules) -> None:
    node4, output = _run(node5_config, action_rules)
    assert check_consistency(output, node4) == []


def test_insufficient_data_remains_separate(node5_config, action_rules) -> None:
    node4, output = _run(node5_config, action_rules)
    assert output.report.insufficient_data_accounts
    main = {a.customer_id for a in output.report.priority_accounts}
    insufficient = {a.customer_id for a in output.report.insufficient_data_accounts}
    assert main.isdisjoint(insufficient)


def test_same_inputs_and_versions_are_bit_identical(node5_config, action_rules) -> None:
    node4, first = _run(node5_config, action_rules)
    second = run_node5(
        node4, node5_config, node3_output=make_sample_inputs()[1], action_rules=action_rules
    )
    assert first.model_dump(mode="json") == second.model_dump(mode="json")

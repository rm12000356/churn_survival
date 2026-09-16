"""Milestone A — final consistency gate (architecture §5.29)."""

from __future__ import annotations

from datetime import date

import pytest

from node5.node import run_node5
from node5.report.consistency import (
    DoNotPublishError,
    check_consistency,
    enforce_consistency,
)
from schemas.node5 import Node5Output, RiskDistribution
from tests.node5.conftest import make_sample_inputs

_BAD_DISTRIBUTION = RiskDistribution(
    critical=99, high=0, medium=0, low=0, insufficient_data=0
)


@pytest.fixture()
def context(node5_config, action_rules):
    node4, node3 = make_sample_inputs()
    output = run_node5(node4, node5_config, node3_output=node3, action_rules=action_rules)
    return node4, node3, output


def _mutate(output: Node5Output, report_updates=None, metadata_updates=None) -> Node5Output:
    report = output.report
    if report_updates:
        report = report.model_copy(update=report_updates)
    metadata = output.metadata
    if metadata_updates:
        metadata = metadata.model_copy(update=metadata_updates)
    return output.model_copy(update={"report": report, "metadata": metadata})


def test_valid_report_passes(context) -> None:
    node4, _, output = context
    assert check_consistency(output, node4) == []


def test_distribution_mismatch_is_blocked(context) -> None:
    node4, _, output = context
    bad = _mutate(output, {"risk_distribution": _BAD_DISTRIBUTION})
    assert any("risk_distribution" in f for f in check_consistency(bad, node4))


def test_reference_date_mismatch_is_blocked(context) -> None:
    node4, _, output = context
    bad = _mutate(output, {"reference_date": date(1999, 1, 1)})
    assert any("reference_date" in f for f in check_consistency(bad, node4))


def test_reordered_priority_accounts_are_blocked(context) -> None:
    node4, _, output = context
    accounts = list(output.report.priority_accounts)
    accounts[0], accounts[1] = accounts[1], accounts[0]
    bad = _mutate(output, {"priority_accounts": accounts})
    assert any("order" in f for f in check_consistency(bad, node4))


def test_score_change_is_blocked(context) -> None:
    node4, _, output = context
    accounts = list(output.report.priority_accounts)
    accounts[0] = accounts[0].model_copy(update={"combined_score": 0.5})
    bad = _mutate(output, {"priority_accounts": accounts})
    assert any("score differs" in f for f in check_consistency(bad, node4))


def test_cross_list_membership_is_blocked(context) -> None:
    node4, _, output = context
    insufficient = [
        *output.report.insufficient_data_accounts,
        output.report.priority_accounts[0],
    ]
    bad = _mutate(output, {"insufficient_data_accounts": insufficient})
    assert any("both" in f for f in check_consistency(bad, node4))


def test_critical_reason_loss_is_blocked(context) -> None:
    node4, _, output = context
    accounts = list(output.report.priority_accounts)
    critical_index = next(
        i for i, account in enumerate(accounts) if account.risk_level.value == "critical"
    )
    accounts[critical_index] = accounts[critical_index].model_copy(
        update={"primary_reasons": []}
    )
    bad = _mutate(output, {"priority_accounts": accounts})
    assert any("critical reason" in f for f in check_consistency(bad, node4))


def test_insufficient_rank_is_blocked(context) -> None:
    node4, _, output = context
    insufficient = list(output.report.insufficient_data_accounts)
    insufficient[0] = insufficient[0].model_copy(update={"rank": 7})
    bad = _mutate(output, {"insufficient_data_accounts": insufficient})
    assert any("no rank" in f for f in check_consistency(bad, node4))


def test_missing_metadata_version_is_blocked(context) -> None:
    node4, _, output = context
    bad = _mutate(output, metadata_updates={"report_version": ""})
    assert any("report_version" in f for f in check_consistency(bad, node4))


def test_enforce_raises_do_not_publish(context) -> None:
    node4, _, output = context
    bad = _mutate(output, {"risk_distribution": _BAD_DISTRIBUTION})
    with pytest.raises(DoNotPublishError):
        enforce_consistency(bad, node4)


# --- F-3: provenance ------------------------------------------------------- #
def test_empty_required_provenance_blocks(context) -> None:
    node4, _, output = context
    for field in (
        "report_version",
        "node2_model_version",
        "node3_signal_version",
        "node4_ranking_version",
        "node4_threshold_version",
        "node4_critical_rules_version",
    ):
        bad = _mutate(output, metadata_updates={field: ""})
        assert any(field in f for f in check_consistency(bad, node4)), field


def test_mixed_version_blocks(context) -> None:
    node4, _, output = context
    account = node4.ranked_accounts[0]
    changed = account.model_copy(
        update={"meta": account.meta.model_copy(update={"ranking_version": "2.0"})}
    )
    mutated = node4.model_copy(
        update={"ranked_accounts": [changed, *node4.ranked_accounts[1:]]}
    )
    assert any("mixed provenance" in f for f in check_consistency(output, mutated))


def test_action_rules_version_required_when_enabled(context) -> None:
    node4, _, output = context
    bad = _mutate(output, metadata_updates={"action_rules_version": None})
    failures = check_consistency(bad, node4, require_action_rules=True)
    assert any("action_rules_version is required" in f for f in failures)

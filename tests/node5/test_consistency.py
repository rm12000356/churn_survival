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
from schemas.enums import CustomerState
from schemas.node4 import (
    ChurnedAccount,
    Node2EvidenceRef,
    Node4Output,
    Node4ProcessingReport,
    Node4Provenance,
    SummaryStats,
)
from schemas.node5 import Node5Output, RiskDistribution
from tests.node5.conftest import REFERENCE_DATE, make_sample_inputs

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


# --- provenance for empty / all-churned portfolios ------------------------- #
def _summary_stats(**overrides: int) -> SummaryStats:
    base = {
        "n_customers": 0,
        "n_critical": 0,
        "n_high": 0,
        "n_medium": 0,
        "n_low": 0,
        "n_insufficient_data": 0,
        "n_churned": 0,
    }
    base.update(overrides)
    return SummaryStats(**base)


def _provenance(node2: str = "mv_all_churned") -> Node4Provenance:
    return Node4Provenance(
        node2_model_version=node2,
        node3_signal_version="n3;test",
        ranking_version="1.3",
        threshold_version="1.0",
        critical_rules_version="1.0",
    )


def test_node4_run_populates_provenance(node5_config) -> None:
    node4, _ = make_sample_inputs()
    assert node4.provenance is not None
    assert node4.provenance.node2_model_version == "mv_test"
    assert node4.provenance.ranking_version


def test_all_churned_portfolio_publishes_with_provenance(node5_config, action_rules) -> None:
    node4 = Node4Output(
        ranked_accounts=[],
        insufficient_data_accounts=[],
        churned_accounts=[
            ChurnedAccount(
                customer_id="Z",
                tenure_days=30.0,
                evidence_refs=Node2EvidenceRef(
                    model_version="mv_all_churned",
                    customer_state=CustomerState.SCORED,
                    feature_refs=[],
                ),
            )
        ],
        summary_stats=_summary_stats(n_churned=1),
        reference_date=REFERENCE_DATE,
        processing_report=Node4ProcessingReport(),
        provenance=_provenance(),
    )
    output = run_node5(node4, node5_config, action_rules=action_rules)
    assert output.report.churned.customer_ids == ["Z"]
    assert output.metadata.node2_model_version == "mv_all_churned"
    assert output.metadata.node4_ranking_version == "1.3"
    assert not any("provenance is incomplete" in w for w in output.processing_report.warnings)


def test_zero_account_portfolio_publishes_with_warning(node5_config, action_rules) -> None:
    node4 = Node4Output(
        ranked_accounts=[],
        insufficient_data_accounts=[],
        churned_accounts=[],
        summary_stats=_summary_stats(),
        reference_date=REFERENCE_DATE,
        processing_report=Node4ProcessingReport(),
        provenance=Node4Provenance(
            node2_model_version="",
            node3_signal_version="",
            ranking_version="1.3",
            threshold_version="1.0",
            critical_rules_version="1.0",
        ),
    )
    output = run_node5(node4, node5_config, action_rules=action_rules)
    assert output.metadata.node4_ranking_version == "1.3"
    assert output.metadata.node2_model_version == ""
    assert any(
        "provenance is incomplete" in w for w in output.processing_report.warnings
    )


def test_accounts_present_with_empty_version_still_blocks(
    node5_config, action_rules
) -> None:
    """Provenance may only be empty when there are no ranked/insufficient accounts."""
    node4, node3 = make_sample_inputs()

    def blank(account):
        refs = account.evidence_refs.model_copy(
            update={
                "node3": account.evidence_refs.node3.model_copy(
                    update={"signal_version": ""}
                )
            }
        )
        return account.model_copy(update={"evidence_refs": refs})

    mutated = node4.model_copy(
        update={
            "ranked_accounts": [blank(a) for a in node4.ranked_accounts],
            "insufficient_data_accounts": [
                blank(a) for a in node4.insufficient_data_accounts
            ],
            "provenance": node4.provenance.model_copy(
                update={"node3_signal_version": ""}
            ),
        }
    )
    with pytest.raises(DoNotPublishError):
        run_node5(mutated, node5_config, node3_output=node3, action_rules=action_rules)


def test_allow_empty_provenance_does_not_relax_report_version(context) -> None:
    node4, _, output = context
    bad = _mutate(output, metadata_updates={"report_version": ""})
    failures = check_consistency(bad, node4, allow_empty_provenance=True)
    assert any("report_version" in f for f in failures)

"""Milestone C — adversarial explanation-boundary tests (architecture §5.28/§5.34)."""

from __future__ import annotations

import json

from node5.node import run_node5
from node5.report.transformer import build_template_explanation
from tests.node5.conftest import (
    FakeLlmClient,
    llm_enabled_config,
    make_sample_inputs,
    make_sample_node4,
)


def _attack(headline: str, summary: str) -> str:
    return json.dumps(
        {"headline": headline, "summary": summary, "reason_explanations": []}
    )


def _run(attack: str, action_rules):
    node4, node3 = make_sample_inputs()
    client = FakeLlmClient([attack])
    return run_node5(
        node4,
        llm_enabled_config(),
        node3_output=node3,
        action_rules=action_rules,
        llm_client=client,
    )


def _template(customer_id: str) -> tuple[str, str]:
    account = next(
        a for a in make_sample_node4().ranked_accounts if a.customer_id == customer_id
    )
    return build_template_explanation(account, customer_id)


def _assert_fell_back(output, customer_id: str) -> None:
    report = next(
        a for a in output.report.priority_accounts if a.customer_id == customer_id
    )
    headline, summary = _template(customer_id)
    assert report.headline == headline
    assert report.summary == summary
    assert output.processing_report.llm_failures >= 1


def test_low_cannot_be_turned_high(action_rules) -> None:
    output = _run(_attack("High risk", "This is a high risk account."), action_rules)
    _assert_fell_back(output, "D")


def test_medium_cannot_be_turned_critical(action_rules) -> None:
    output = _run(_attack("Critical risk", "This account is critical."), action_rules)
    _assert_fell_back(output, "C")


def test_cancellation_intent_cannot_be_invented(action_rules) -> None:
    output = _run(
        _attack("High risk", "The customer plans to cancel their subscription."),
        action_rules,
    )
    _assert_fell_back(output, "B")


def test_customer_facts_cannot_be_invented(action_rules) -> None:
    output = _run(
        _attack("Critical risk", "Globex Corporation acquired the account."),
        action_rules,
    )
    _assert_fell_back(output, "A")


def test_percentages_cannot_be_invented(action_rules) -> None:
    output = _run(
        _attack("Critical risk", "The risk score increased by 37%."), action_rules
    )
    _assert_fell_back(output, "A")


def test_timestamps_cannot_be_invented(action_rules) -> None:
    output = _run(
        _attack("Critical risk", "The issue first occurred at 14:30."), action_rules
    )
    _assert_fell_back(output, "A")


def test_unsupported_evidence_cannot_be_cited(action_rules) -> None:
    output = _run(
        _attack("Critical risk", "See message msg_ghost_1 for details."), action_rules
    )
    _assert_fell_back(output, "A")


def test_unsupported_recommendation_is_rejected(action_rules) -> None:
    output = _run(
        _attack("Low risk", "We recommend offering a discount immediately."),
        action_rules,
    )
    _assert_fell_back(output, "D")


def test_node4_cannot_be_contradicted(action_rules) -> None:
    output = _run(
        _attack("Critical risk", "The customer will remain active."), action_rules
    )
    _assert_fell_back(output, "A")


def test_confidence_cannot_be_stated_as_probability(action_rules) -> None:
    output = _run(
        _attack("Critical risk", "There is a 91% chance of churn."), action_rules
    )
    _assert_fell_back(output, "A")


def test_insufficient_data_is_never_low_risk(action_rules) -> None:
    output = _run(_attack("Low risk", "This account is not at risk."), action_rules)
    insufficient = output.report.insufficient_data_accounts
    assert insufficient
    for report in insufficient:
        assert report.risk_level.value == "insufficient_data"
        assert report.rank is None

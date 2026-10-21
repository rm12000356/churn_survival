"""Milestone A — account transformation + customer context (architecture §5.8–§5.14)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from node5.report.transformer import (
    CustomerProfile,
    build_customer_report,
    build_template_explanation,
    coerce_customer_data,
    display_name_for,
    map_quantitative,
    map_reasons,
    map_support,
    sanitize_display_name,
)
from schemas.enums import ReportRiskLevel
from tests.node5.conftest import make_sample_node4


def _by_id(output):
    return {a.customer_id: a for a in [*output.ranked_accounts, *output.insufficient_data_accounts]}


def test_display_name_falls_back_to_customer_id() -> None:
    assert display_name_for("A", {}) == "A"
    assert display_name_for("A", {"A": CustomerProfile(name="Acme")}) == "Acme"


def test_coerce_customer_data_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        coerce_customer_data({"A": {"name": "Acme", "secret": "x"}})


def test_map_reasons_preserves_order_and_statement() -> None:
    account = make_sample_node4().ranked_accounts[0]
    reasons = map_reasons(account)
    assert [reason.reason_type for reason in reasons] == [
        reason.reason_type for reason in account.primary_reasons
    ]
    assert all(reason.statement for reason in reasons)


def test_map_quantitative_and_support_copy_values() -> None:
    account = make_sample_node4().ranked_accounts[0]
    quantitative = map_quantitative(account)
    assert quantitative.risk_score == account.quantitative.risk_score
    assert quantitative.survival_prob_90d == account.quantitative.survival_prob_90d
    support = map_support(account)
    assert support.support_data_status == account.qualitative.support_data_status
    assert support.signal_strength == account.qualitative.signal_strength


def test_template_explanation_is_deterministic() -> None:
    account = make_sample_node4().ranked_accounts[0]
    first = build_template_explanation(account, "Acme")
    second = build_template_explanation(account, "Acme")
    assert first == second
    assert first[0].startswith("Critical")


def test_insufficient_report_keeps_state_and_no_rank() -> None:
    output = make_sample_node4()
    account = output.insufficient_data_accounts[0]
    report = build_customer_report(
        account, display_name="E", recommended_action=None, evidence=[]
    )
    assert report.rank is None
    assert report.risk_level == ReportRiskLevel.INSUFFICIENT_DATA
    headline, summary = build_template_explanation(account, "E")
    assert "Insufficient_data" not in headline
    assert "Insufficient data" in headline
    assert "Insufficient_data" not in summary
    assert "Insufficient data" in summary


# --- F-4: malformed top_flags --------------------------------------------- #
def test_map_support_surfaces_malformed_top_flags() -> None:
    base = make_sample_node4().ranked_accounts[0]
    malformed = [
        None,
        "bad",
        123,
        {},
        {"flag_type": "billing_complaint"},
        {"severity": "high"},
    ]
    account = base.model_copy(
        update={"qualitative": base.qualitative.model_copy(update={"top_flags": malformed})}
    )
    errors: list[dict] = []
    support = map_support(account, errors)
    assert support.top_flags == []
    assert len(errors) == len(malformed)
    assert all(error["code"] == "INVALID_TOP_FLAG" for error in errors)


def test_map_support_keeps_valid_flags() -> None:
    base = make_sample_node4().ranked_accounts[1]  # B has a billing complaint
    errors: list[dict] = []
    support = map_support(base, errors)
    assert errors == []
    assert support.top_flags


# --- F-8: display-name sanitization --------------------------------------- #
def test_sanitize_display_name() -> None:
    assert sanitize_display_name("  Acme   Corp \n") == "Acme Corp"
    assert sanitize_display_name("Bad\x00Name") == "Bad Name"
    assert len(sanitize_display_name("x" * 300)) <= 120
    assert sanitize_display_name("Acme Corp") == "Acme Corp"


def test_display_name_falls_back_when_name_is_only_control_chars() -> None:
    assert display_name_for("A", {"A": CustomerProfile(name="\x00\x01")}) == "A"


def test_display_name_is_bounded() -> None:
    long_name = "Acme " * 100
    cleaned = display_name_for("A", {"A": CustomerProfile(name=long_name)})
    assert len(cleaned) <= 120

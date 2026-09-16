"""Milestone A — deterministic recommendations (architecture §5.18/§5.19, D-REC)."""

from __future__ import annotations

from node5.report.recommendations import RECOMMENDATION_PRIORITY, select_recommendation
from schemas.enums import FlagType
from tests.node5.conftest import make_sample_node4


def _by_id(output):
    return {a.customer_id: a for a in output.ranked_accounts}


def test_cancellation_intent_wins(action_rules) -> None:
    account = _by_id(make_sample_node4())["A"]
    action, flag_type = select_recommendation(account, action_rules, enabled=True)
    assert flag_type == FlagType.CANCELLATION_INTENT
    assert "cancellation" in action.lower()


def test_billing_complaint_selected(action_rules) -> None:
    account = _by_id(make_sample_node4())["B"]
    action, flag_type = select_recommendation(account, action_rules, enabled=True)
    assert flag_type == FlagType.BILLING_COMPLAINT
    assert action is not None


def test_positive_feedback_yields_no_recommendation(action_rules) -> None:
    account = _by_id(make_sample_node4())["D"]
    action, flag_type = select_recommendation(account, action_rules, enabled=True)
    assert action is None
    assert flag_type is None


def test_disabled_and_missing_rules(action_rules) -> None:
    account = _by_id(make_sample_node4())["A"]
    assert select_recommendation(account, action_rules, enabled=False) == (None, None)
    assert select_recommendation(account, None, enabled=True) == (None, None)


def test_priority_order_is_the_locked_sequence() -> None:
    assert RECOMMENDATION_PRIORITY[:4] == (
        FlagType.CANCELLATION_INTENT,
        FlagType.RENEWAL_OR_CONTRACT_CONCERN,
        FlagType.PRODUCT_BUG_OR_OUTAGE,
        FlagType.POOR_SUPPORT_EXPERIENCE,
    )
    assert RECOMMENDATION_PRIORITY[-1] == FlagType.OTHER

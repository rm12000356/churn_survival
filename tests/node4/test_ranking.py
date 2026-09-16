"""Task 5.11 — deterministic 7-key sort, sequential ranks, rank stability (§4.19/§4.20)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from config.loader import load_node4_config
from node4.node import run_node4
from node4.ranking import rank_accounts, sort_insufficient_data_accounts, sort_key
from schemas.node4 import RankedAccount
from tests.node4.conftest import make_node2, make_node3, make_signal

META = {
    "ranked_at": datetime(2026, 8, 15, tzinfo=UTC).isoformat(),
    "ranking_version": "1.0",
    "threshold_version": "1.0",
    "critical_rules_version": "1.0",
}


def _account(
    customer_id: str,
    *,
    level: str = "medium",
    score: float = 0.5,
    confidence: float = 0.5,
    churn: bool = False,
    strength: str = "none",
    quant: float | None = 0.5,
) -> RankedAccount:
    payload: dict[str, Any] = {
        "customer_id": customer_id,
        "rank": None,
        "combined_risk_level": level,
        "combined_score": score,
        "combined_confidence": confidence,
        "quantitative": {
            "model_status": "READY",
            "risk_score": quant,
            "survival_prob_90d": None if quant is None else round(1 - quant, 3),
            "normalized_risk": quant,
            "top_drivers": [],
            "customer_state": "scored",
        },
        "qualitative": {
            "support_data_status": "no_data" if strength == "none" else "sufficient_data",
            "signal_strength": strength,
            "overall_signal_confidence": confidence,
            "churn_language_detected": churn,
            "top_flags": [],
            "escalation_signal": False,
        },
        "primary_reasons": [],
        "evidence_refs": {
            "node2": {"model_version": "mv", "customer_state": "scored", "feature_refs": []},
            "node3": {"signal_version": "", "thread_ids": [], "message_ids": []},
        },
        "explanation": None,
        "meta": META,
    }
    return RankedAccount.model_validate(payload)


def test_sort_priority_risk_level_first() -> None:
    config = load_node4_config("1")
    accounts = [
        _account("low", level="low", score=1.0),
        _account("critical", level="critical", score=0.1),
        _account("medium", level="medium", score=0.9),
        _account("high", level="high", score=0.5),
    ]
    ordered = rank_accounts(accounts, config)
    assert [a.customer_id for a in ordered] == ["critical", "high", "medium", "low"]
    assert [a.rank for a in ordered] == [1, 2, 3, 4]


def test_sort_combined_score_descending() -> None:
    config = load_node4_config("1")
    accounts = [_account("a", score=0.4), _account("b", score=0.9), _account("c", score=0.6)]
    ordered = rank_accounts(accounts, config)
    assert [a.customer_id for a in ordered] == ["b", "c", "a"]


def test_sort_churn_language_first() -> None:
    config = load_node4_config("1")
    accounts = [
        _account("without", score=0.5, churn=False),
        _account("with", score=0.5, churn=True),
    ]
    assert [a.customer_id for a in rank_accounts(accounts, config)] == ["with", "without"]


def test_sort_strongest_signal_order() -> None:
    config = load_node4_config("1")
    accounts = [
        _account("none", strength="none"),
        _account("weak", strength="weak"),
        _account("strong", strength="strong"),
        _account("moderate", strength="moderate"),
    ]
    assert [a.customer_id for a in rank_accounts(accounts, config)] == [
        "strong",
        "moderate",
        "weak",
        "none",
    ]


def test_sort_quantitative_risk_desc_none_last() -> None:
    config = load_node4_config("1")
    accounts = [
        _account("none", quant=None),
        _account("low", quant=0.1),
        _account("high", quant=0.9),
    ]
    assert [a.customer_id for a in rank_accounts(accounts, config)] == ["high", "low", "none"]


def test_sort_confidence_desc_then_customer_id() -> None:
    config = load_node4_config("1")
    accounts = [
        _account("b", confidence=0.5),
        _account("a", confidence=0.5),
        _account("c", confidence=0.9),
    ]
    assert [a.customer_id for a in rank_accounts(accounts, config)] == ["c", "a", "b"]


def test_sort_key_is_total_and_deterministic() -> None:
    config = load_node4_config("1")
    account = _account("x")
    assert sort_key(account, config) == sort_key(account, config)


def test_insufficient_accounts_rank_none_and_sorted_by_id() -> None:
    accounts = [
        _account("b", level="insufficient_data", quant=None, strength="none"),
        _account("a", level="insufficient_data", quant=None, strength="none"),
    ]
    ordered = sort_insufficient_data_accounts(accounts)
    assert [a.customer_id for a in ordered] == ["a", "b"]
    assert all(a.rank is None for a in ordered)


def test_rank_stability_adding_unrelated_customer() -> None:
    config = load_node4_config("1")
    base2 = make_node2(["A", "B"], risk_scores=[0.9, 0.1], survival_90=[0.1, 0.9])
    node3 = make_node3([make_signal("A"), make_signal("B")])
    before = run_node4(base2, node3, config)

    base3 = make_node2(["A", "B", "C"], risk_scores=[0.9, 0.1, 0.05], survival_90=[0.1, 0.9, 0.95])
    after = run_node4(base3, node3, config)

    before_by_id = {a.customer_id: a for a in before.ranked_accounts}
    after_by_id = {a.customer_id: a for a in after.ranked_accounts}
    for customer_id in ("A", "B"):
        assert before_by_id[customer_id].combined_score == after_by_id[customer_id].combined_score
        assert (
            before_by_id[customer_id].combined_risk_level
            == after_by_id[customer_id].combined_risk_level
        )
        assert (
            before_by_id[customer_id].combined_confidence
            == after_by_id[customer_id].combined_confidence
        )
    assert before.ranked_accounts[0].customer_id == after.ranked_accounts[0].customer_id

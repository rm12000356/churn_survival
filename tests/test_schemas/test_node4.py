from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.node4 import Node4Output, RankedAccount

RANKED = {
    "customer_id": "cus_8f3a2b1c",
    "rank": 1,
    "combined_risk_level": "critical",
    "combined_score": 0.87,
    "combined_confidence": 0.91,
    "quantitative": {
        "model_status": "READY",
        "risk_score": 0.9,
        "survival_prob_90d": 0.1,
        "normalized_risk": 0.85,
        "top_drivers": ["declining_usage"],
        "customer_state": "scored",
    },
    "qualitative": {
        "support_data_status": "sufficient_data",
        "signal_strength": "strong",
        "overall_signal_confidence": 0.78,
        "churn_language_detected": True,
        "top_flags": [
            {
                "flag_type": "cancellation_intent",
                "severity": "high",
                "signal_strength": "strong",
            }
        ],
        "escalation_signal": True,
    },
    "primary_reasons": [
        {
            "reason_type": "critical_cancellation_intent",
            "source": "node3",
            "severity": "high",
            "evidence_ref": {"thread_ids": ["thr_1001"]},
        }
    ],
    "evidence_refs": {
        "node2": {
            "model_version": "v0.1.0",
            "customer_state": "scored",
            "feature_refs": ["plan_tier_pro"],
        },
        "node3": {
            "signal_version": "v1",
            "thread_ids": ["thr_1001"],
            "message_ids": ["msg_1001_1"],
        },
    },
    "explanation": None,
    "meta": {
        "ranked_at": "2026-08-17T10:00:00Z",
        "ranking_version": "1.0",
        "threshold_version": "1.0",
        "critical_rules_version": "1.0",
    },
}


def test_ranked_account_round_trip() -> None:
    account = RankedAccount.model_validate(RANKED)
    assert account.rank == 1
    assert account.combined_risk_level.value == "critical"
    assert account.primary_reasons[0].reason_type.value == "critical_cancellation_intent"
    assert account.evidence_refs.node3.thread_ids == ["thr_1001"]


def test_insufficient_data_account_rank_none() -> None:
    payload = {**RANKED, "rank": None, "combined_risk_level": "insufficient_data"}
    account = RankedAccount.model_validate(payload)
    assert account.rank is None


def test_invalid_risk_level_rejected() -> None:
    payload = {**RANKED, "combined_risk_level": "extreme"}
    with pytest.raises(ValidationError):
        RankedAccount.model_validate(payload)


def test_invalid_reason_source_rejected() -> None:
    payload = {**RANKED, "primary_reasons": [{**RANKED["primary_reasons"][0], "source": "node7"}]}
    with pytest.raises(ValidationError):
        RankedAccount.model_validate(payload)


def test_invalid_customer_state_rejected() -> None:
    payload = {
        **RANKED,
        "quantitative": {**RANKED["quantitative"], "customer_state": "unknown"},
    }
    with pytest.raises(ValidationError):
        RankedAccount.model_validate(payload)


def test_score_out_of_range_rejected() -> None:
    payload = {**RANKED, "combined_score": 1.4}
    with pytest.raises(ValidationError):
        RankedAccount.model_validate(payload)


def test_node4_output_round_trip() -> None:
    output = Node4Output.model_validate(
        {
            "ranked_accounts": [RANKED],
            "insufficient_data_accounts": [],
            "summary_stats": {
                "n_customers": 1,
                "n_critical": 1,
                "n_high": 0,
                "n_medium": 0,
                "n_low": 0,
                "n_insufficient_data": 0,
            },
            "reference_date": "2026-08-15",
            "processing_report": {"warnings": [], "errors": []},
        }
    )
    assert output.summary_stats.n_critical == 1
    assert len(output.ranked_accounts) == 1
    assert output.reference_date.isoformat() == "2026-08-15"

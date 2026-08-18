from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.node5 import CustomerReport, Node5Output

CUSTOMER_REPORT = {
    "customer_id": "cus_8f3a2b1c",
    "display_name": "Acme Corp",
    "rank": 1,
    "risk_level": "critical",
    "combined_score": 0.87,
    "combined_confidence": 0.91,
    "headline": "Critical — cancellation intent detected",
    "summary": "Explicit cancellation intent was detected in recent support interactions.",
    "primary_reasons": [
        {
            "reason_type": "critical_cancellation_intent",
            "source": "node3",
            "severity": "critical",
            "statement": "Explicit cancellation intent detected in support interactions.",
            "evidence_ref": {"thread_ids": ["thr_1001"]},
        }
    ],
    "quantitative_summary": {
        "risk_score": 0.9,
        "survival_prob_90d": 0.1,
        "top_drivers": ["declining_usage"],
        "customer_state": "scored",
    },
    "support_summary": {
        "support_data_status": "sufficient_data",
        "signal_strength": "strong",
        "churn_language_detected": True,
        "escalation_signal": True,
        "top_flags": [
            {
                "flag_type": "cancellation_intent",
                "severity": "high",
                "signal_strength": "strong",
            }
        ],
    },
    "evidence": [
        {
            "source": "node3",
            "description": (
                "Customer expressed cancellation intent in a recent support interaction."
            ),
            "node2_reference": None,
            "node3_reference": {
                "thread_id": "thr_1001",
                "message_id": "msg_1001_1",
                "timestamp": "2026-07-20T14:30:00Z",
                "evidence_text": "We are considering cancelling before the renewal.",
            },
        }
    ],
    "data_quality_notes": [],
    "recommended_action": "Contact the account to discuss cancellation concerns.",
}


def test_customer_report_round_trip() -> None:
    report = CustomerReport.model_validate(CUSTOMER_REPORT)
    assert report.risk_level.value == "critical"
    assert report.primary_reasons[0].severity.value == "critical"
    assert report.evidence[0].node3_reference is not None
    assert report.evidence[0].node3_reference.message_id == "msg_1001_1"


def test_insufficient_data_level_rejected_in_report() -> None:
    payload = {**CUSTOMER_REPORT, "risk_level": "insufficient_data"}
    with pytest.raises(ValidationError):
        CustomerReport.model_validate(payload)


def test_report_severity_accepts_critical() -> None:
    reason = {**CUSTOMER_REPORT["primary_reasons"][0], "severity": "critical"}
    payload = {**CUSTOMER_REPORT, "primary_reasons": [reason]}
    CustomerReport.model_validate(payload)


def test_evidence_never_invents_quote() -> None:
    report = CustomerReport.model_validate(CUSTOMER_REPORT)
    for evidence in report.evidence:
        if evidence.node3_reference is not None:
            assert evidence.node3_reference.evidence_text  # must carry real text


def test_node5_output_round_trip() -> None:
    output = Node5Output.model_validate(
        {
            "report": {
                "title": "Churn Risk Report",
                "reference_date": "2026-08-15",
                "executive_summary": "1 of 1 customers is Critical.",
                "risk_distribution": {
                    "critical": 1,
                    "high": 0,
                    "medium": 0,
                    "low": 0,
                    "insufficient_data": 0,
                },
                "priority_accounts": [CUSTOMER_REPORT],
                "insufficient_data_accounts": [],
                "data_quality": {"notes": []},
                "methodology": "Cox PH + support signals.",
            },
            "metadata": {
                "report_version": "1.0",
                "node2_model_version": "v0.1.0",
                "node3_signal_version": "v1",
                "node4_ranking_version": "1.0",
                "node4_threshold_version": "1.0",
                "node4_critical_rules_version": "1.0",
                "prompt_version": None,
                "llm_model_version": None,
                "reference_date": "2026-08-15",
                "generated_at": "2026-08-17T10:00:00Z",
            },
            "processing_report": {
                "n_accounts": 1,
                "n_accounts_reported": 1,
                "n_insufficient_data": 0,
                "llm_calls": 0,
                "llm_failures": 0,
                "validation_errors": 0,
                "warnings": [],
                "errors": [],
            },
        }
    )
    assert output.report.risk_distribution.critical == 1
    assert output.metadata.report_version == "1.0"
    assert output.processing_report.n_accounts == 1


def test_node5_output_missing_metadata_rejected() -> None:
    base = {
        "report": {
            "title": "x",
            "reference_date": "2026-08-15",
            "executive_summary": "x",
            "risk_distribution": {
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "insufficient_data": 0,
            },
            "priority_accounts": [],
            "insufficient_data_accounts": [],
            "data_quality": {"notes": []},
            "methodology": "x",
        },
        "processing_report": {
            "n_accounts": 0,
            "n_accounts_reported": 0,
            "n_insufficient_data": 0,
            "llm_calls": 0,
            "llm_failures": 0,
            "validation_errors": 0,
            "warnings": [],
            "errors": [],
        },
    }
    with pytest.raises(ValidationError):
        Node5Output.model_validate(base)

"""Task 5.10 — confidence mapping and missing upstream nodes (§4.14/§4.15)."""

from __future__ import annotations

from config.loader import load_node4_config
from node4.confidence import (
    QUANT_CONFIDENCE_BY_STATUS,
    combined_confidence,
    quantitative_confidence,
)
from node4.node import (
    WARNING_NODE2_UNAVAILABLE,
    WARNING_NODE3_UNAVAILABLE,
    run_node4,
)
from schemas.enums import ModelStatus
from tests.node4.conftest import make_flag, make_node2, make_node3, make_signal


def test_status_to_confidence_mapping() -> None:
    assert QUANT_CONFIDENCE_BY_STATUS == {
        ModelStatus.READY: 1.00,
        ModelStatus.WARNING: 0.70,
        ModelStatus.FALLBACK: 0.45,
        ModelStatus.INSUFFICIENT_DATA: 0.00,
        ModelStatus.FAILED: 0.00,
    }
    assert quantitative_confidence(ModelStatus.FALLBACK) == 0.45


def test_combined_confidence_weighted_and_rounded() -> None:
    config = load_node4_config("1")
    assert combined_confidence(1.0, 0.0, config) == 0.55
    assert combined_confidence(0.70, 1.0, config) == 0.835
    assert combined_confidence(0.0, 0.0, config) == 0.0


def test_combined_confidence_clamped() -> None:
    config = load_node4_config("1")
    assert combined_confidence(1.0, 1.0, config) == 1.0
    assert combined_confidence(-1.0, -1.0, config) == 0.0


def test_node3_unavailable_warning_and_zero_qualitative() -> None:
    config = load_node4_config("1")
    node2 = make_node2(["A"], risk_scores=[0.9], survival_90=[0.1])
    output = run_node4(node2, None, config)
    assert WARNING_NODE3_UNAVAILABLE in output.processing_report.warnings
    account = output.ranked_accounts[0]
    assert account.qualitative.support_data_status.value == "no_data"
    assert account.qualitative.overall_signal_confidence == 0.0


def test_node2_unavailable_warning_and_zero_quantitative() -> None:
    config = load_node4_config("1")
    node3 = make_node3([make_signal("A", risk_flags=[make_flag("billing_complaint")])])
    output = run_node4(None, node3, config)
    assert WARNING_NODE2_UNAVAILABLE in output.processing_report.warnings
    account = output.ranked_accounts[0]
    assert account.quantitative.normalized_risk is None
    assert account.combined_confidence == 0.36


def test_risk_level_and_confidence_are_independent() -> None:
    config = load_node4_config("1")
    # Low combined risk but high confidence: both values move independently.
    node2 = make_node2(
        ["A"], risk_scores=[0.0], survival_90=[1.0], model_status="READY"
    )
    node3 = make_node3([make_signal("A", overall_signal_confidence=0.8)])
    output = run_node4(node2, node3, config)
    account = output.ranked_accounts[0]
    assert account.combined_risk_level.value == "low"
    assert account.combined_confidence == 0.91

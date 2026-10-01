"""Optional support input → quantitative-only synthesis (architecture §4.14a).

Support threads are an optional run input. When a run supplies none, Node 4
config v2 (``quantitative_only_without_support``) must not score every customer
as "no support data": the combined score and confidence come from the survival
model alone, and no per-account ``missing_support_data`` / no-data conflict
reason is emitted — one run-level warning says so instead. v1 keeps the
original §4.14 behaviour bit-for-bit.
"""

from __future__ import annotations

import pytest

from config.loader import load_node4_config
from node4.node import WARNING_SUPPORT_NOT_SUPPLIED, run_node4
from schemas.enums import ReasonType, RiskLevel
from tests.node4.conftest import make_node2, make_node3, make_signal, make_thread

IDS = ["C-HIGH", "C-MED", "C-LOW"]
# 90d survival → quantitative risk = 1 - s90: 0.80 (high), 0.45 (medium), 0.10 (low)
SURVIVAL_90 = [0.20, 0.55, 0.90]
# cox_ph WARNING → quantitative confidence proxy 0.70 (QUANT_CONFIDENCE_BY_STATUS)
QUANT_CONF = 0.70


def _node2():
    return make_node2(IDS, survival_90=SURVIVAL_90, model_status="WARNING")


def _baseline_node3():
    """What the orchestrator emits with no support input: all no_data, zero threads."""
    return make_node3(
        [
            make_signal(
                cid,
                support_data_status="no_data",
                overall_signal_confidence=0.0,
                n_threads_in_window=0,
            )
            for cid in IDS
        ]
    )


def _reason_types(account) -> set[str]:
    return {reason.reason_type.value for reason in account.primary_reasons}


def _by_id(output):
    return {a.customer_id: a for a in [*output.ranked_accounts, *output.insufficient_data_accounts]}


@pytest.fixture(scope="module")
def v2():
    return load_node4_config("2")


@pytest.fixture(scope="module")
def v1():
    return load_node4_config("1")


def test_v2_no_support_scores_from_model_alone(v2) -> None:
    out = run_node4(_node2(), _baseline_node3(), v2, support_supplied=False)
    accounts = _by_id(out)

    # Score = quantitative risk itself, so High is reachable without support.
    assert accounts["C-HIGH"].combined_score == pytest.approx(0.80)
    assert accounts["C-HIGH"].combined_risk_level == RiskLevel.HIGH
    assert accounts["C-MED"].combined_risk_level == RiskLevel.MEDIUM
    assert accounts["C-LOW"].combined_risk_level == RiskLevel.LOW

    # Confidence = model confidence, not 0.55 × 0.70 = 0.385.
    assert {a.combined_confidence for a in accounts.values()} == {QUANT_CONF}


def test_v2_no_support_emits_no_per_account_support_reasons(v2) -> None:
    out = run_node4(_node2(), _baseline_node3(), v2, support_supplied=False)
    for account in _by_id(out).values():
        types = _reason_types(account)
        assert ReasonType.MISSING_SUPPORT_DATA.value not in types
        # High quant + no_data used to be reported as a quant/qual "conflict".
        assert ReasonType.QUANTITATIVE_QUALITATIVE_CONFLICT.value not in types
    assert WARNING_SUPPORT_NOT_SUPPLIED in out.processing_report.warnings


def test_v2_support_supplied_keeps_per_customer_missing_support(v2) -> None:
    """Support WAS supplied; a customer with zero threads is a real per-customer gap."""
    node3 = make_node3(
        [
            make_signal("C-HIGH", support_data_status="sufficient_data"),
            make_signal("C-MED", support_data_status="sufficient_data"),
            make_signal(
                "C-LOW",
                support_data_status="no_data",
                overall_signal_confidence=0.0,
                n_threads_in_window=0,
            ),
        ],
        thread_signals=[make_thread("t1", "C-HIGH"), make_thread("t2", "C-MED")],
    )
    out = run_node4(_node2(), node3, v2, support_supplied=True)
    accounts = _by_id(out)
    assert ReasonType.MISSING_SUPPORT_DATA.value in _reason_types(accounts["C-LOW"])
    # Weighted confidence as before: 0.55 × 0.70 + 0.45 × 0.0
    assert accounts["C-LOW"].combined_confidence == pytest.approx(0.385)
    assert WARNING_SUPPORT_NOT_SUPPLIED not in out.processing_report.warnings


def test_v2_infers_no_support_from_zero_processed_threads(v2) -> None:
    """CLI/direct callers pass no flag: zero processed threads means none supplied."""
    out = run_node4(_node2(), _baseline_node3(), v2)
    assert WARNING_SUPPORT_NOT_SUPPLIED in out.processing_report.warnings
    assert {a.combined_confidence for a in _by_id(out).values()} == {QUANT_CONF}


def test_v2_no_support_without_quant_is_still_insufficient_data(v2) -> None:
    """No model score AND no support stays first-class INSUFFICIENT_DATA (D-8)."""
    node2 = make_node2(
        IDS,
        survival_90=[0.20, 0.55],  # scored subset only (Node 2 alignment contract)
        states=["scored", "scored", "not_enough_data"],
        model_status="WARNING",
    )
    out = run_node4(node2, _baseline_node3(), v2, support_supplied=False)
    insufficient = {a.customer_id for a in out.insufficient_data_accounts}
    assert insufficient == {"C-LOW"}
    assert out.insufficient_data_accounts[0].combined_risk_level == RiskLevel.INSUFFICIENT_DATA
    assert {a.customer_id for a in out.ranked_accounts} == {"C-HIGH", "C-MED"}


def test_v1_keeps_original_behaviour(v1) -> None:
    """v1 is frozen: same inputs → the original §4.14 output."""
    out = run_node4(_node2(), _baseline_node3(), v1, support_supplied=False)
    accounts = _by_id(out)
    assert accounts["C-HIGH"].combined_score == pytest.approx(0.60 * 0.80)
    assert accounts["C-HIGH"].combined_risk_level == RiskLevel.MEDIUM
    assert {a.combined_confidence for a in accounts.values()} == {0.385}
    assert all(
        ReasonType.MISSING_SUPPORT_DATA.value in _reason_types(a) for a in accounts.values()
    )
    assert WARNING_SUPPORT_NOT_SUPPLIED not in out.processing_report.warnings


def test_v2_matches_v1_when_support_is_supplied(v1, v2) -> None:
    """The switch only acts when no support was supplied."""
    node3 = make_node3(
        [make_signal(cid, support_data_status="sufficient_data") for cid in IDS],
        thread_signals=[make_thread(f"t{i}", cid) for i, cid in enumerate(IDS)],
    )
    a = run_node4(_node2(), node3, v1, support_supplied=True)
    b = run_node4(_node2(), node3, v2, support_supplied=True)

    def decisions(out):
        return [
            (x.customer_id, x.rank, x.combined_risk_level, x.combined_score,
             x.combined_confidence, [r.reason_type for r in x.primary_reasons])
            for x in out.ranked_accounts
        ]

    assert decisions(a) == decisions(b)

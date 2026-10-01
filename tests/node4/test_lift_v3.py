"""Node 4 v3 — lift-scaled risk, churned split, per-customer confidence (phase 10).

D-R2 lift map, D-R3 churned split, D-R4 conf_v2, D-R5 tail customers are missing
(never Low). v1/v2 outputs must not change when Node 2 carries the new fields.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from config.loader import load_node4_config
from config.models import ConfidenceFactors, Node4Config
from node4.confidence import customer_quant_confidence
from node4.node import WARNING_NO_FORWARD_SURVIVAL, run_node4
from node4.quantitative import lift_normalized_risk
from schemas.enums import ModelStatus, ReasonType, RiskLevel
from schemas.node2 import ForwardHorizonResult, Node2Output
from tests.node4.conftest import make_flag, make_node2, make_node3, make_signal, make_thread

LIFT_POINTS = [(0.0, 0.0), (1.0, 0.20), (1.5, 0.40), (3.0, 0.70), (6.0, 1.0)]
FACTORS = ConfidenceFactors(
    precision_max_ci_width=0.20,
    precision_floor=0.5,
    history_maturity_days=180,
    history_floor=0.5,
)
N_FILLER = 36
FILLER_P = 0.02


def _book(
    *,
    filler: int = N_FILLER,
    extra: list[tuple[str, float | None, int, float]] | None = None,
    forward: bool = True,
    model_status: str = "WARNING",
) -> Node2Output:
    """A Cox book: ``filler`` average customers + ``extra`` (id, churn_p, event, tenure).

    ``churn_p is None`` on an active customer means "beyond follow-up".
    """
    rows: list[tuple[str, float | None, int, float]] = [
        (f"F-{i:03d}", FILLER_P, 0, 400.0) for i in range(filler)
    ]
    rows += extra or []
    rows.sort(key=lambda row: row[0])
    ids = [row[0] for row in rows]
    node2 = make_node2(
        ids,
        survival_90=[0.99] * len(ids),
        risk_scores=[0.01] * len(ids),
        model_status=model_status,
    )
    if not forward:
        return node2
    values: list[float | None] = []
    ci: list[list[float | None]] = []
    for _, p, event, _ in rows:
        if p is None or event == 1:
            values.append(None)
            ci.append([None, None])
        else:
            values.append(1.0 - p)
            ci.append([1.0 - p - 0.01, min(1.0, 1.0 - p + 0.01)])
    return node2.model_copy(
        update={
            "customer_tenure_days": [row[3] for row in rows],
            "customer_event_observed": [row[2] for row in rows],
            "forward_survival": {"90d": ForwardHorizonResult(values=values, ci=ci)},
            "max_follow_up_days": 1000.0,
        }
    )


def _accounts(output: Any) -> dict[str, Any]:
    return {a.customer_id: a for a in [*output.ranked_accounts, *output.insufficient_data_accounts]}


@pytest.fixture(scope="module")
def v3() -> Node4Config:
    return load_node4_config("3")


# --- lift map ---------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("lift", "expected"),
    [(0.0, 0.0), (1.0, 0.20), (1.5, 0.40), (2.0, 0.50), (3.0, 0.70), (6.0, 1.0), (10.0, 1.0)],
)
def test_lift_map_knots_and_clamp(lift: float, expected: float) -> None:
    risk, measured = lift_normalized_risk(lift * 0.04, 0.04, LIFT_POINTS)
    assert risk == pytest.approx(expected)
    assert measured == pytest.approx(lift)


def test_lift_map_rejects_zero_base() -> None:
    with pytest.raises(ValueError):
        lift_normalized_risk(0.1, 0.0, LIFT_POINTS)


def test_v3_config_shape(v3: Node4Config) -> None:
    assert v3.risk_scale == "lift"
    assert v3.normalization_version == "risk_norm_v2"
    assert v3.separate_churned is True
    assert v3.confidence_version == "conf_v2"
    assert v3.lift_points == LIFT_POINTS
    assert v3.confidence_factors == FACTORS


@pytest.mark.parametrize(
    "update",
    [
        {"lift_points": None},
        {"lift_points": [(0.0, 0.0), (2.0, 0.5), (1.0, 0.6)]},
        {"lift_points": [(0.5, 0.0), (2.0, 0.5)]},
        {"lift_points": [(0.0, 0.0), (2.0, 1.5)]},
    ],
)
def test_lift_points_validation(v3: Node4Config, update: dict[str, Any]) -> None:
    data = v3.model_dump()
    data.update(update)
    with pytest.raises(ValidationError):
        Node4Config.model_validate(data)


def test_v1_v2_defaults_keep_absolute_scale() -> None:
    for version in ("1", "2"):
        config = load_node4_config(version)
        assert config.risk_scale == "absolute"
        assert config.separate_churned is False
        assert config.confidence_factors is None


# --- levels, churned split, partition --------------------------------------- #


def test_lift_levels_and_churned_split(v3: Node4Config) -> None:
    extra = [
        ("C-HIGH", 0.12, 0, 400.0),
        ("C-MED", 0.05, 0, 400.0),
        ("C-GONE-1", None, 1, 100.0),
        ("C-GONE-2", None, 1, 50.0),
    ]
    node2 = _book(extra=extra)
    output = run_node4(node2, None, v3)
    accounts = _accounts(output)

    probabilities = [FILLER_P] * N_FILLER + [0.12, 0.05]
    base = sum(probabilities) / len(probabilities)
    high_risk, high_lift = lift_normalized_risk(0.12, base, LIFT_POINTS)
    assert accounts["C-HIGH"].quantitative.normalized_risk == high_risk
    assert accounts["C-HIGH"].quantitative.lift_vs_base == high_lift
    assert accounts["C-HIGH"].quantitative.churn_prob_90d_forward == pytest.approx(0.12)
    assert accounts["C-HIGH"].quantitative.forward_status == "available"
    assert accounts["C-HIGH"].combined_risk_level == RiskLevel.HIGH
    assert accounts["C-MED"].combined_risk_level == RiskLevel.MEDIUM
    assert accounts["F-000"].combined_risk_level == RiskLevel.LOW

    churned_ids = [a.customer_id for a in output.churned_accounts]
    assert churned_ids == ["C-GONE-1", "C-GONE-2"]
    assert output.churned_accounts[0].tenure_days == 100.0
    assert output.summary_stats.n_churned == 2
    assert not set(churned_ids) & set(accounts)
    # D-R3 partition: every customer in exactly one list.
    every = [*accounts, *churned_ids]
    assert sorted(every) == sorted(node2.customer_ids)
    assert len(every) == len(set(every))
    assert output.summary_stats.n_customers == len(accounts)
    assert any("book average" in w for w in output.processing_report.warnings)

    reasons = {r.reason_type for r in accounts["C-HIGH"].primary_reasons}
    assert ReasonType.HIGH_QUANTITATIVE_RISK in reasons
    ref = next(
        r.evidence_ref
        for r in accounts["C-HIGH"].primary_reasons
        if r.reason_type == ReasonType.HIGH_QUANTITATIVE_RISK
    )
    assert isinstance(ref, dict)
    assert ref["lift_vs_base"] == high_lift
    assert ref["base_rate_90d"] == pytest.approx(base, abs=1e-6)


def test_beyond_follow_up_is_missing_not_low(v3: Node4Config) -> None:
    node2 = _book(extra=[("C-TAIL", None, 0, 900.0)])
    output = run_node4(node2, None, v3)
    tail = _accounts(output)["C-TAIL"]
    assert tail.combined_risk_level == RiskLevel.INSUFFICIENT_DATA
    assert tail in output.insufficient_data_accounts
    assert tail.quantitative.forward_status == "beyond_follow_up"
    assert tail.quantitative.normalized_risk is None
    assert tail.combined_confidence == 0.0
    assert not [e for e in output.processing_report.errors if e["code"] == "PARTIAL_ALIGNMENT"]
    assert any("beyond the model's follow-up" in w for w in output.processing_report.warnings)


def test_tail_customer_with_support_is_ranked_on_support(v3: Node4Config) -> None:
    node2 = _book(extra=[("C-TAIL", None, 0, 900.0)])
    signal = make_signal(
        "C-TAIL",
        risk_flags=[make_flag("cancellation_intent", signal_strength="strong")],
        signal_strength="strong",
    )
    output = run_node4(node2, make_node3([signal], [make_thread("t1", "C-TAIL")]), v3)
    tail = _accounts(output)["C-TAIL"]
    assert tail.combined_risk_level == RiskLevel.CRITICAL
    assert tail.quantitative.forward_status == "beyond_follow_up"


def test_base_rate_fallback_below_minimum(v3: Node4Config) -> None:
    node2 = _book(filler=10, extra=[("C-HIGH", 0.12, 0, 400.0)])
    output = run_node4(node2, None, v3)
    accounts = _accounts(output)
    assert accounts["C-HIGH"].quantitative.lift_vs_base is None
    assert accounts["C-HIGH"].quantitative.forward_status is None
    # Absolute path: 1 - S(90d) = 0.01.
    assert accounts["C-HIGH"].quantitative.normalized_risk == pytest.approx(0.01)
    assert any("absolute risk scale" in w for w in output.processing_report.warnings)


def test_zero_base_rate_falls_back(v3: Node4Config) -> None:
    node2 = _book()
    zero = node2.model_copy(
        update={
            "forward_survival": {
                "90d": ForwardHorizonResult(
                    values=[1.0] * N_FILLER, ci=[[None, None]] * N_FILLER
                )
            }
        }
    )
    output = run_node4(zero, None, v3)
    assert any("absolute risk scale" in w for w in output.processing_report.warnings)


def test_node2_without_forward_fields_falls_back(v3: Node4Config) -> None:
    output = run_node4(_book(forward=False), None, v3)
    assert WARNING_NO_FORWARD_SURVIVAL in output.processing_report.warnings
    assert output.summary_stats.n_churned == 0


def test_v2_unchanged_by_new_node2_fields() -> None:
    v2 = load_node4_config("2")
    extra = [("C-HIGH", 0.12, 0, 400.0), ("C-GONE", None, 1, 100.0)]
    with_forward = run_node4(_book(extra=extra), None, v2)
    without = run_node4(_book(extra=extra, forward=False), None, v2)
    assert with_forward.model_dump() == without.model_dump()
    assert with_forward.churned_accounts == []
    assert all(a.confidence_factors is None for a in with_forward.ranked_accounts)


def test_v3_is_deterministic(v3: Node4Config) -> None:
    node2 = _book(extra=[("C-HIGH", 0.12, 0, 400.0), ("C-GONE", None, 1, 100.0)])
    first = run_node4(node2, None, v3).model_dump_json()
    second = run_node4(node2, None, v3).model_dump_json()
    assert first == second


# --- critical rules now reachable ------------------------------------------- #


def test_high_quant_plus_contract_concern_can_fire(v3: Node4Config) -> None:
    node2 = _book(extra=[("C-HIGH", 0.20, 0, 400.0)])
    signal = make_signal(
        "C-HIGH",
        risk_flags=[make_flag("renewal_or_contract_concern", signal_strength="strong")],
        signal_strength="strong",
    )
    output = run_node4(node2, make_node3([signal], [make_thread("t1", "C-HIGH")]), v3)
    account = _accounts(output)["C-HIGH"]
    assert account.quantitative.normalized_risk is not None
    assert account.quantitative.normalized_risk >= v3.quantitative_thresholds.high
    assert account.combined_risk_level == RiskLevel.CRITICAL
    reasons = {r.reason_type for r in account.primary_reasons}
    assert ReasonType.CRITICAL_HIGH_QUANT_PLUS_CONTRACT_CONCERN in reasons


# --- conf_v2 ---------------------------------------------------------------- #


def test_customer_quant_confidence_factors() -> None:
    value, model, precision, history = customer_quant_confidence(
        ModelStatus.WARNING, 0.05, 90.0, FACTORS
    )
    assert model == 0.70
    assert precision == pytest.approx(0.75)
    assert history == pytest.approx(0.75)
    assert value == pytest.approx(round(0.70 * 0.75 * 0.75, 3))


def test_confidence_none_ci_floor_and_wide_ci_and_maturity() -> None:
    _, _, precision, _ = customer_quant_confidence(ModelStatus.READY, None, 400.0, FACTORS)
    assert precision == 0.5
    _, _, precision, history = customer_quant_confidence(ModelStatus.READY, 0.9, 400.0, FACTORS)
    assert precision == 0.0
    assert history == 1.0
    _, _, _, history = customer_quant_confidence(ModelStatus.READY, 0.0, 0.0, FACTORS)
    assert history == 0.5


def test_confidence_varies_per_customer_quant_only(v3: Node4Config) -> None:
    node2 = _book(extra=[("C-NEW", 0.05, 0, 30.0), ("C-OLD", 0.05, 0, 400.0)])
    accounts = _accounts(run_node4(node2, None, v3))
    new, old = accounts["C-NEW"], accounts["C-OLD"]
    assert new.combined_confidence < old.combined_confidence
    assert old.confidence_factors is not None
    assert old.confidence_factors.support is None
    assert old.combined_confidence == old.confidence_factors.quantitative
    # CI width 0.02 → precision 0.9; mature tenure → history 1.0; WARNING → 0.70.
    assert old.confidence_factors.precision == pytest.approx(0.9)
    assert old.confidence_factors.history == 1.0
    assert old.combined_confidence == pytest.approx(0.63)


def test_confidence_with_support_uses_weights(v3: Node4Config) -> None:
    node2 = _book(extra=[("C-OLD", 0.05, 0, 400.0)])
    signal = make_signal("C-OLD", overall_signal_confidence=0.8)
    output = run_node4(node2, make_node3([signal], [make_thread("t1", "C-OLD")]), v3)
    account = _accounts(output)["C-OLD"]
    assert account.confidence_factors is not None
    assert account.confidence_factors.support == 0.8
    expected = round(
        v3.confidence_weights.quantitative * account.confidence_factors.quantitative
        + v3.confidence_weights.qualitative * 0.8,
        3,
    )
    assert account.combined_confidence == expected


def test_partial_alignment_zeroes_quant_confidence(v3: Node4Config) -> None:
    node2 = _book()
    assert node2.forward_survival is not None
    assert node2.risk_scores is not None
    forward = node2.forward_survival["90d"]
    horizon = node2.survival_probabilities["90d"]
    assert horizon.values is not None
    short = node2.model_copy(
        update={
            "forward_survival": {
                "90d": ForwardHorizonResult(values=forward.values[:-1], ci=forward.ci[:-1])
            },
            "survival_probabilities": {
                "90d": horizon.model_copy(update={"values": horizon.values[:-1]})
            },
            "risk_scores": node2.risk_scores[:-1],
        }
    )
    output = run_node4(short, None, v3)
    last = _accounts(output)[node2.customer_ids[-1]]
    assert last.confidence_factors is not None
    assert last.confidence_factors.quantitative == 0.0
    assert any(e["code"] == "PARTIAL_ALIGNMENT" for e in output.processing_report.errors)

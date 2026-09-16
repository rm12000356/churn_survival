"""Task 5.3 — quantitative normalization precedence, D-1, D-2 (§4.4)."""

from __future__ import annotations

import pytest

from config.loader import load_node4_config
from node4.quantitative import normalize_risk_score, quantitative_risk_score, top_drivers
from schemas.enums import ModelType
from tests.node4.conftest import make_association


def test_survival_probability_takes_precedence() -> None:
    assert quantitative_risk_score(0.8, 0.5) == 0.2


def test_risk_score_fallback() -> None:
    assert quantitative_risk_score(None, 0.34567) == 0.346


def test_none_when_neither_available() -> None:
    assert quantitative_risk_score(None, None) is None


def test_missing_quantitative_is_not_zero() -> None:
    assert quantitative_risk_score(None, None) is None


@pytest.mark.parametrize("risk_score", [0.0, 0.123, 0.5, 0.98765, 1.0])
def test_normalization_is_identity_clamp(risk_score: float) -> None:
    assert normalize_risk_score(risk_score) == max(0.0, min(1.0, round(risk_score, 3)))


def test_normalization_clamps_out_of_range() -> None:
    assert normalize_risk_score(-0.5) == 0.0
    assert normalize_risk_score(1.5) == 1.0


@pytest.mark.parametrize("risk_score", [0.0, 0.25, 0.5, 0.75, 1.0])
def test_90d_path_matches_risk_score_path_when_consistent(risk_score: float) -> None:
    survival_prob_90d = 1.0 - risk_score
    assert quantitative_risk_score(survival_prob_90d, risk_score) == normalize_risk_score(
        risk_score
    )


def test_survival_path_is_bounded() -> None:
    assert quantitative_risk_score(0.0, None) == 1.0
    assert quantitative_risk_score(1.0, None) == 0.0


def test_top_drivers_requires_cox_ph() -> None:
    config = load_node4_config("1")
    associations = [make_association("usage_frequency", coefficient=0.5, hazard_ratio=1.6)]
    assert top_drivers(associations, ModelType.COX_PH, config) == ["usage_frequency"]
    assert top_drivers(associations, ModelType.KAPLAN_MEIER, config) == []
    assert top_drivers(associations, ModelType.NONE, config) == []
    assert top_drivers(None, ModelType.COX_PH, config) == []


def test_top_drivers_keeps_only_raised_sorted_and_capped() -> None:
    config = load_node4_config("1")
    associations = [
        make_association("b_feature", coefficient=0.2, hazard_ratio=1.2),
        make_association("a_feature", coefficient=0.9, hazard_ratio=2.0),
        make_association("c_feature", coefficient=0.9, hazard_ratio=1.8),
        make_association("protective", coefficient=-0.5, hazard_ratio=0.6),
        make_association("d_feature", coefficient=0.1, hazard_ratio=1.1),
    ]
    # coefficient desc, tie-break feature asc, cap at top_drivers_max (5)
    assert top_drivers(associations, ModelType.COX_PH, config) == [
        "a_feature",
        "c_feature",
        "b_feature",
        "d_feature",
    ]


def test_top_drivers_respects_configured_cap() -> None:
    config = load_node4_config("1").model_copy(update={"top_drivers_max": 1})
    associations = [
        make_association("a", coefficient=0.9, hazard_ratio=2.0),
        make_association("b", coefficient=0.5, hazard_ratio=1.5),
    ]
    assert top_drivers(associations, ModelType.COX_PH, config) == ["a"]

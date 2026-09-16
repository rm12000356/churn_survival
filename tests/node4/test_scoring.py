"""Task 5.6 — combined score and strong-agreement bonus (§4.7)."""

from __future__ import annotations

import pytest

from config.loader import load_node4_config
from node4.rules import classify_risk_level
from node4.scoring import combined_score
from schemas.enums import OverallSignalStrength, RiskLevel


def test_weighted_sum() -> None:
    config = load_node4_config("1")
    assert combined_score(1.0, 0.0, OverallSignalStrength.NONE, config) == 0.6
    assert combined_score(0.0, 1.0, OverallSignalStrength.NONE, config) == 0.4


def test_missing_quantitative_counts_as_zero_in_arithmetic() -> None:
    config = load_node4_config("1")
    assert combined_score(None, 0.5, OverallSignalStrength.MODERATE, config) == 0.2


def test_strong_agreement_bonus_applied() -> None:
    config = load_node4_config("1")
    # 0.6*0.8 + 0.4*1.0 + 0.05 = 0.93
    assert combined_score(0.8, 1.0, OverallSignalStrength.STRONG, config) == 0.93


def test_no_bonus_without_strong_qualitative_signal() -> None:
    config = load_node4_config("1")
    assert combined_score(0.8, 0.66, OverallSignalStrength.MODERATE, config) == 0.744


def test_no_bonus_without_high_quantitative_risk() -> None:
    config = load_node4_config("1")
    assert combined_score(0.5, 1.0, OverallSignalStrength.STRONG, config) == 0.7


def test_no_bonus_when_quantitative_missing() -> None:
    config = load_node4_config("1")
    assert combined_score(None, 1.0, OverallSignalStrength.STRONG, config) == 0.4


def test_score_clamped_to_one() -> None:
    config = load_node4_config("1")
    assert combined_score(1.0, 1.0, OverallSignalStrength.STRONG, config) == 1.0


@pytest.mark.parametrize("qual", [0.0, 0.33, 0.66, 1.0])
def test_score_bounds(qual: float) -> None:
    config = load_node4_config("1")
    value = combined_score(0.7, qual, OverallSignalStrength.STRONG, config)
    assert 0.0 <= value <= 1.0


# --------------------------------------------------------------------------- #
# F-1 — combined score is not rounded before threshold classification (§4.12)
# --------------------------------------------------------------------------- #
BOUNDARY_TARGETS = [
    0.3994,
    0.3995,
    0.3996,
    0.3998,
    0.4000,
    0.4002,
    0.4004,
    0.4005,
    0.4006,
    0.6994,
    0.6995,
    0.6996,
    0.6998,
    0.7000,
    0.7002,
    0.7004,
    0.7005,
    0.7006,
]


def _contract_level(value: float) -> RiskLevel:
    if value < 0.40:
        return RiskLevel.LOW
    if value < 0.70:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


@pytest.mark.parametrize("target", BOUNDARY_TARGETS)
def test_combined_score_not_rounded_before_threshold(target: float) -> None:
    config = load_node4_config("1")
    # 0.6 * 0.5 + 0.4 * qual == target  ->  qual == (target - 0.3) / 0.4
    qualitative = (target - 0.3) / 0.4
    raw = combined_score(0.5, qualitative, OverallSignalStrength.NONE, config)
    assert raw == pytest.approx(target, abs=1e-9)
    assert classify_risk_level(raw, [], config) == _contract_level(target)


def test_raw_03998_is_low_not_rounded_up() -> None:
    config = load_node4_config("1")
    raw = combined_score(0.5, 0.2495, OverallSignalStrength.NONE, config)
    assert raw == pytest.approx(0.3998)
    assert raw < 0.40
    assert classify_risk_level(raw, [], config) == RiskLevel.LOW


def test_raw_06998_is_medium_not_rounded_up() -> None:
    config = load_node4_config("1")
    raw = combined_score(0.5, 0.9995, OverallSignalStrength.NONE, config)
    assert raw == pytest.approx(0.6998)
    assert raw < 0.70
    assert classify_risk_level(raw, [], config) == RiskLevel.MEDIUM


def test_combined_score_is_clamped_and_unrounded() -> None:
    config = load_node4_config("1")
    # The stored value is the actual weighted sum, not a 3-dp rounding of it.
    raw = combined_score(0.5, 0.2495, OverallSignalStrength.NONE, config)
    assert raw == pytest.approx(0.6 * 0.5 + 0.4 * 0.2495, abs=1e-12)
    assert combined_score(1.0, 1.0, OverallSignalStrength.STRONG, config) == 1.0

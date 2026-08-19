"""Deterministic interpretation layer (ROADMAP Task 3.9/§2.10)."""

from __future__ import annotations

from node2.interpretation import build_feature_association, interpret_hazard_ratio


def test_hazard_ratio_identity() -> None:
    text = interpret_hazard_ratio(1.0)
    assert "no difference" in text


def test_hazard_ratio_above_one() -> None:
    text = interpret_hazard_ratio(1.52)
    assert "52%" in text
    assert "higher" in text


def test_hazard_ratio_below_one() -> None:
    text = interpret_hazard_ratio(0.7)
    assert "30%" in text
    assert "lower" in text


def test_build_feature_association_block() -> None:
    block = build_feature_association("plan_tier_pro", 0.42, 1.52)
    assert "coefficient = 0.42" in block
    assert "hazard_ratio = exp(0.42) ≈ 1.52" in block
    assert "higher" in block


def test_numeric_effect_uses_unit_phrasing() -> None:
    text = interpret_hazard_ratio(1.52, kind="numeric")
    assert "one-unit increase" in text
    assert "associated with" in text
    assert "52%" in text
    assert "higher" in text
    assert "category" not in text


def test_effect_confident_when_reliable() -> None:
    text = interpret_hazard_ratio(
        1.52, kind="numeric", ci_lower=1.1, ci_upper=2.0, p_value=0.01
    )
    assert "not statistically distinguishable" not in text
    assert "one-unit increase" in text


def test_effect_caution_when_ci_crosses_one() -> None:
    text = interpret_hazard_ratio(1.52, ci_lower=0.9, ci_upper=1.6)
    assert "not statistically distinguishable from no effect" in text
    assert "confidence interval includes 1.0" in text
    assert "higher" in text


def test_effect_caution_when_p_above_threshold() -> None:
    text = interpret_hazard_ratio(0.7, p_value=0.4)
    assert "not statistically distinguishable from no effect" in text
    assert "p ≥ 0.05" in text
    assert "30%" in text
    assert "lower" in text

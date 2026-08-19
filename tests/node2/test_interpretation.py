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

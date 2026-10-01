"""Deterministic interpretation layer (ROADMAP Task 3.9/§2.10)."""

from __future__ import annotations

from node2.interpretation import (
    build_feature_association,
    interpret_contribution,
    interpret_hazard_ratio,
)
from schemas.node2 import FeatureContribution


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


# --------------------------------------------------------------------------- #
# Per-customer contribution text (§2.12b, 2026-10-01)
# --------------------------------------------------------------------------- #


def _contribution(**overrides: object) -> FeatureContribution:
    base: dict[str, object] = {
        "feature": "usage_frequency",
        "column": "usage_frequency",
        "kind": "numeric",
        "value": 3.0,
        "reference": 12.4,
        "coefficient": -0.1697,
        "hazard_ratio": 0.844,
        "contribution": 1.595,
        "reliable": True,
    }
    base.update(overrides)
    return FeatureContribution.model_validate(base)


def test_contribution_below_reference_with_protective_feature() -> None:
    assert interpret_contribution(_contribution()) == (
        "usage_frequency (3) is below the model reference profile (12.4); the model "
        "associates a lower value with a higher churn hazard (HR ≈ 0.84 per unit)."
    )


def test_contribution_above_reference_mirrors() -> None:
    text = interpret_contribution(_contribution(value=20.0, contribution=-1.29))
    assert "is above the model reference profile (12.4)" in text
    assert "a higher value with a lower churn hazard" in text
    risky = interpret_contribution(
        _contribution(feature="tickets", column="tickets", value=5.0, reference=2.0,
                      hazard_ratio=1.2, coefficient=0.18, contribution=0.55)
    )
    assert "a higher value with a higher churn hazard (HR ≈ 1.20 per unit)" in risky


def test_contribution_equal_to_reference() -> None:
    assert interpret_contribution(_contribution(value=12.4, contribution=0.0)) == (
        "usage_frequency (12.4) matches the model reference profile."
    )


def test_contribution_categorical() -> None:
    text = interpret_contribution(
        _contribution(feature="plan_tier", column="plan_tier_starter", kind="categorical",
                      value="starter", reference="enterprise", hazard_ratio=1.417,
                      coefficient=0.3485, contribution=0.3485)
    )
    assert text == (
        "plan_tier = starter is associated with a higher churn hazard than the reference "
        "category enterprise (HR ≈ 1.42)."
    )


def test_contribution_unreliable_caution_and_locked_terminology() -> None:
    text = interpret_contribution(_contribution(reliable=False))
    assert text.endswith(
        "(This association is not statistically distinguishable from no effect.)"
    )
    for banned in ("portfolio average", "relative risk", "causes", "because"):
        assert banned not in text

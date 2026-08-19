"""Deterministic interpretation layer (architecture §2.10, ROADMAP Task 3.9).

Statistical results -> fixed text. ``exp(β)`` (hazard ratio) renders into a
deterministic sentence; an LLM may later polish tone but must never invent the
statistical meaning.
"""

from __future__ import annotations

import math

# Hard-coded significance threshold for v1; a config knob can come later.
SIGNIFICANCE_ALPHA = 0.05


def interpret_hazard_ratio(
    hazard_ratio: float,
    *,
    kind: str = "categorical",
    ci_lower: float | None = None,
    ci_upper: float | None = None,
    p_value: float | None = None,
    p_threshold: float = SIGNIFICANCE_ALPHA,
) -> str:
    """Render a hazard ratio into the §2.10 sentence form.

    Categorical predictors are phrased relative to the reference category;
    numeric predictors are phrased per unit increase. When the effect is not
    statistically reliable (confidence interval includes 1.0 or p above the
    threshold), the text carries an explicit caution instead of a confident
    directional claim. Language stays non-causal.
    """
    if math.isclose(hazard_ratio, 1.0, abs_tol=1e-9):
        if kind == "numeric":
            return (
                "A one-unit increase in this feature is associated with no difference "
                "in instantaneous churn hazard, holding other variables in the model "
                "constant."
            )
        return (
            "Customers in this category had no difference in instantaneous churn "
            "hazard relative to the reference category, holding other variables "
            "in the model constant."
        )
    percent = round(abs(hazard_ratio - 1.0) * 100)
    direction = "higher" if hazard_ratio > 1.0 else "lower"
    if kind == "numeric":
        base = (
            f"A one-unit increase in this feature is associated with an estimated "
            f"{percent}% {direction} instantaneous churn hazard, holding other "
            "variables in the model constant."
        )
    else:
        base = (
            f"Customers in this category had an estimated {percent}% {direction} "
            "instantaneous churn hazard than the reference category, holding other "
            "variables in the model constant."
        )
    reasons: list[str] = []
    if ci_lower is not None and ci_upper is not None and ci_lower <= 1.0 <= ci_upper:
        reasons.append("the confidence interval includes 1.0")
    if p_value is not None and p_value >= p_threshold:
        reasons.append(f"p ≥ {p_threshold:.2f}")
    if reasons:
        base += (
            " This effect is not statistically distinguishable from no effect ("
            + " and ".join(reasons)
            + ")."
        )
    return base


def build_feature_association(feature: str, coefficient: float, hazard_ratio: float) -> str:
    """Compose the full §2.10 text block for a single feature."""
    return (
        f"coefficient = {coefficient:.2f}\n"
        f"hazard_ratio = exp({coefficient:.2f}) ≈ {hazard_ratio:.2f}\n"
        f"→ {interpret_hazard_ratio(hazard_ratio)}"
    )

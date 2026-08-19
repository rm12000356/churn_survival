"""Deterministic interpretation layer (architecture §2.10, ROADMAP Task 3.9).

Statistical results -> fixed text. ``exp(β)`` (hazard ratio) renders into a
deterministic sentence; an LLM may later polish tone but must never invent the
statistical meaning.
"""

from __future__ import annotations

import math


def interpret_hazard_ratio(hazard_ratio: float) -> str:
    """Render a hazard ratio into the §2.10 sentence form.

    Example: coefficient 0.42 -> hazard_ratio ~ 1.52 ->
    "Customers in this category had an estimated 52% higher instantaneous churn
    hazard than the reference category, holding other variables in the model
    constant."
    """
    if math.isclose(hazard_ratio, 1.0, abs_tol=1e-9):
        return (
            "Customers in this category had no difference in instantaneous churn "
            "hazard relative to the reference category, holding other variables "
            "in the model constant."
        )
    percent = round(abs(hazard_ratio - 1.0) * 100)
    direction = "higher" if hazard_ratio > 1.0 else "lower"
    return (
        f"Customers in this category had an estimated {percent}% {direction} "
        "instantaneous churn hazard than the reference category, holding other "
        "variables in the model constant."
    )


def build_feature_association(feature: str, coefficient: float, hazard_ratio: float) -> str:
    """Compose the full §2.10 text block for a single feature."""
    return (
        f"coefficient = {coefficient:.2f}\n"
        f"hazard_ratio = exp({coefficient:.2f}) ≈ {hazard_ratio:.2f}\n"
        f"→ {interpret_hazard_ratio(hazard_ratio)}"
    )

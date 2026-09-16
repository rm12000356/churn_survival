"""Node 4 quantitative normalization (architecture §4.4, ROADMAP Task 5.3).

Locked decisions D-1 (identity-clamp normalization) and D-2 (top drivers). Node 2
``risk_score`` is already ``clip(1 - S(t_ref), 0, 1)`` (``node2/cox.py``), so the
normalization is not a second scoring system — it is a deterministic clamp of an
already-probability quantity.
"""

from __future__ import annotations

from collections.abc import Sequence

from config.models import Node4Config
from schemas.enums import ModelType
from schemas.node2 import FeatureAssociation


def normalize_risk_score(risk_score: float) -> float:
    """D-1: clamp(round(v, 3), 0, 1). Never a second scoring system."""
    return max(0.0, min(1.0, round(risk_score, 3)))


def quantitative_risk_score(
    survival_prob_90d: float | None,
    risk_score: float | None,
) -> float | None:
    """Quantitative risk in [0, 1] (§4.4).

    Precedence: available 90d survival probability, then the Node 2 risk score,
    then ``None``. A missing score is never converted into evidence of low risk.
    """
    if survival_prob_90d is not None:
        return max(0.0, min(1.0, round(1.0 - survival_prob_90d, 3)))
    if risk_score is not None:
        return normalize_risk_score(risk_score)
    return None


def top_drivers(
    feature_associations: Sequence[FeatureAssociation] | None,
    model_type: ModelType | None,
    config: Node4Config,
) -> list[str]:
    """D-2: deterministic top drivers; only for a CoxPH model, never LLM-derived."""
    if model_type != ModelType.COX_PH or not feature_associations:
        return []
    raised = [item for item in feature_associations if item.hazard_ratio > 1.0]
    raised.sort(key=lambda item: (-item.coefficient, item.feature))
    return [item.feature for item in raised[: config.top_drivers_max]]

"""Node 4 combined score (architecture §4.7, ROADMAP Task 5.6).

Linear weighted sum, strong-agreement bonus, and a final [0, 1] clamp. A missing
quantitative score contributes ``0.0`` to the weighted arithmetic only — it is
never stored as a zero score. The agreement bonus may raise the score but can
never by itself produce a Critical classification (critical rules are explicit).

The returned score is **not rounded**: risk-level thresholds operate on the
actual clamped value (architecture §4.12), so a raw score just below a threshold
must not be promoted by display rounding.
"""

from __future__ import annotations

from config.models import Node4Config
from schemas.enums import OverallSignalStrength


def combined_score(
    quantitative_score: float | None,
    qualitative_score: float,
    strongest_strength: OverallSignalStrength,
    config: Node4Config,
) -> float:
    """§4.7 weighted sum + §4.7.1 strong-agreement bonus, clamped to [0, 1]."""
    quant_component = quantitative_score if quantitative_score is not None else 0.0
    score = (
        config.quantitative_weight * quant_component
        + config.qualitative_weight * qualitative_score
    )
    strong_agreement = (
        quantitative_score is not None
        and quantitative_score >= config.quantitative_thresholds.high
        and strongest_strength == OverallSignalStrength.STRONG
    )
    if strong_agreement:
        score += config.agreement_bonus
    return max(0.0, min(1.0, score))

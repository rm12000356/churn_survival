from __future__ import annotations

from config.models import Node4Config
from schemas.enums import OverallSignalStrength


def combined_score(
    quantitative_score: float | None,
    qualitative_score: float,
    strongest_strength: OverallSignalStrength,
    config: Node4Config,
    *,
    quantitative_only: bool = False,
) -> float:
    quant_component = quantitative_score if quantitative_score is not None else 0.0
    if quantitative_only:
        return max(0.0, min(1.0, quant_component))
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

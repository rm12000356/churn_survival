from __future__ import annotations

from config.models import ConfidenceFactors, Node4Config
from schemas.enums import ModelStatus

QUANT_CONFIDENCE_BY_STATUS: dict[ModelStatus, float] = {
    ModelStatus.READY: 1.00,
    ModelStatus.WARNING: 0.70,
    ModelStatus.FALLBACK: 0.45,
    ModelStatus.INSUFFICIENT_DATA: 0.00,
    ModelStatus.FAILED: 0.00,
}


def quantitative_confidence(model_status: ModelStatus) -> float:
    return QUANT_CONFIDENCE_BY_STATUS[model_status]


def customer_quant_confidence(
    model_status: ModelStatus,
    ci_width: float | None,
    tenure_days: float | None,
    factors: ConfidenceFactors,
) -> tuple[float, float, float, float]:
    model = QUANT_CONFIDENCE_BY_STATUS[model_status]
    if ci_width is None:
        precision = factors.precision_floor
    else:
        precision = 1.0 - max(0.0, min(1.0, ci_width / factors.precision_max_ci_width))
    tenure = max(0.0, tenure_days or 0.0)
    maturity = min(1.0, tenure / factors.history_maturity_days)
    history = factors.history_floor + (1.0 - factors.history_floor) * maturity
    model, precision, history = round(model, 3), round(precision, 3), round(history, 3)
    value = max(0.0, min(1.0, round(model * precision * history, 3)))
    return value, model, precision, history


def combined_confidence(
    quantitative_confidence: float,
    qualitative_confidence: float,
    config: Node4Config,
    *,
    quantitative_only: bool = False,
) -> float:
    if quantitative_only:
        return max(0.0, min(1.0, round(quantitative_confidence, 3)))
    value = (
        config.confidence_weights.quantitative * quantitative_confidence
        + config.confidence_weights.qualitative * qualitative_confidence
    )
    return max(0.0, min(1.0, round(value, 3)))

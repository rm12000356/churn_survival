"""Node 4 confidence calculation (architecture §4.14/§4.15, ROADMAP Task 5.10).

Risk level and confidence are independent. The quantitative proxy is derived
from the run-level Node 2 model status; the qualitative confidence is taken
directly from Node 3. A missing upstream node contributes zero confidence and an
exact warning.
"""

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
    """conf_v2 (phase 10, D-R4): ``(quantitative, model, precision, history)``.

    - ``model``: the run-level ceiling ``QUANT_CONFIDENCE_BY_STATUS``.
    - ``precision``: ``1 − clamp(ci_width / precision_max_ci_width)``; a CI that
      cannot be computed gets ``precision_floor`` (unknown, not perfect or zero).
    - ``history``: ``floor + (1 − floor) · min(1, tenure / maturity)`` — a new
      customer's estimate rests on little of their own history.

    Each factor and the product are rounded to 3 dp.
    """
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
    """Weighted, rounded to 3 dp, clamped to [0, 1].

    ``quantitative_confidence`` is normally ``QUANT_CONFIDENCE_BY_STATUS`` of the
    run-level Node 2 model status, but a partial-alignment customer (D-6) passes
    ``0.0`` explicitly.

    ``quantitative_only`` (§4.14a): the run supplied no support input, so an
    optional input the user skipped does not dilute confidence — the result is
    the quantitative confidence alone.
    """
    if quantitative_only:
        return max(0.0, min(1.0, round(quantitative_confidence, 3)))
    value = (
        config.confidence_weights.quantitative * quantitative_confidence
        + config.confidence_weights.qualitative * qualitative_confidence
    )
    return max(0.0, min(1.0, round(value, 3)))

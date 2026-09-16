"""Node 4 confidence calculation (architecture §4.14/§4.15, ROADMAP Task 5.10).

Risk level and confidence are independent. The quantitative proxy is derived
from the run-level Node 2 model status; the qualitative confidence is taken
directly from Node 3. A missing upstream node contributes zero confidence and an
exact warning.
"""

from __future__ import annotations

from config.models import Node4Config
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


def combined_confidence(
    quantitative_confidence: float,
    qualitative_confidence: float,
    config: Node4Config,
) -> float:
    """Weighted, rounded to 3 dp, clamped to [0, 1].

    ``quantitative_confidence`` is normally ``QUANT_CONFIDENCE_BY_STATUS`` of the
    run-level Node 2 model status, but a partial-alignment customer (D-6) passes
    ``0.0`` explicitly.
    """
    value = (
        config.confidence_weights.quantitative * quantitative_confidence
        + config.confidence_weights.qualitative * qualitative_confidence
    )
    return max(0.0, min(1.0, round(value, 3)))

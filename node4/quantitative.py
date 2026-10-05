from __future__ import annotations

from collections.abc import Sequence

from config.models import Node4Config
from schemas.enums import ModelType
from schemas.node2 import DriverDetail, FeatureAssociation, FeatureContribution


def normalize_risk_score(risk_score: float) -> float:
    return max(0.0, min(1.0, round(risk_score, 3)))


def quantitative_risk_score(
    survival_prob_90d: float | None,
    risk_score: float | None,
) -> float | None:
    if survival_prob_90d is not None:
        return max(0.0, min(1.0, round(1.0 - survival_prob_90d, 3)))
    if risk_score is not None:
        return normalize_risk_score(risk_score)
    return None


def lift_normalized_risk(
    churn_prob: float,
    base_rate: float,
    lift_points: Sequence[tuple[float, float]],
) -> tuple[float, float]:
    if base_rate <= 0:
        raise ValueError("base_rate must be positive")
    lift = max(0.0, churn_prob) / base_rate
    xs = [x for x, _ in lift_points]
    ys = [y for _, y in lift_points]
    if lift >= xs[-1]:
        risk = ys[-1]
    else:
        risk = ys[0]
        for (x0, y0), (x1, y1) in zip(lift_points, lift_points[1:], strict=False):
            if x0 <= lift <= x1:
                risk = y0 + (y1 - y0) * (lift - x0) / (x1 - x0)
                break
    return max(0.0, min(1.0, round(risk, 3))), round(lift, 3)


def top_drivers(
    feature_associations: Sequence[FeatureAssociation] | None,
    model_type: ModelType | None,
    config: Node4Config,
) -> list[str]:
    if model_type != ModelType.COX_PH or not feature_associations:
        return []
    raised = [item for item in feature_associations if item.hazard_ratio > 1.0]
    raised.sort(key=lambda item: (-item.coefficient, item.feature))
    return [item.feature for item in raised[: config.top_drivers_max]]


def driver_details_for_customer(
    contributions: Sequence[FeatureContribution] | None,
    config: Node4Config,
) -> list[DriverDetail]:
    if not contributions:
        return []
    kept = [
        item
        for item in contributions
        if item.contribution > 0.0
        and (item.reliable or not config.drivers_require_reliable)
    ]
    kept.sort(key=lambda item: (-item.contribution, item.feature))
    return [
        DriverDetail(
            feature=item.feature,
            kind=item.kind,
            value=item.value,
            reference=item.reference,
            contribution=item.contribution,
            hazard_ratio=item.hazard_ratio,
            reliable=item.reliable,
        )
        for item in kept[: config.top_drivers_max]
    ]


def top_drivers_for_customer(
    contributions: Sequence[FeatureContribution] | None,
    config: Node4Config,
) -> list[str]:
    return [detail.feature for detail in driver_details_for_customer(contributions, config)]

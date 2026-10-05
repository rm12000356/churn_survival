from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from config.models import Node2Config
from node2.matrix import modeling_values, usable_predictors
from schemas.enums import CustomerState


def classify_customer(record: Any, predictors: Sequence[str], config: Node2Config) -> CustomerState:
    usable = usable_predictors(modeling_values(record), predictors)
    tenure = float(record.tenure)

    if tenure <= 0:
        return CustomerState.NOT_ENOUGH_DATA

    if (
        tenure <= config.cold_start_max_tenure_days
        and len(usable) < config.cold_start_min_behavioral_features
    ):
        return CustomerState.NOT_ENOUGH_DATA

    if len(predictors) > 0 and len(usable) == 0:
        return CustomerState.EXCLUDED

    if len(usable) < len(predictors):
        return CustomerState.EXCLUDED

    return CustomerState.SCORED


def classify_customers(
    records: Sequence[Any], predictors: Sequence[str], config: Node2Config
) -> dict[str, CustomerState]:
    return {record.customer_id: classify_customer(record, predictors, config) for record in records}

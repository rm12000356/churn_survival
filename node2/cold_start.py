"""Cold-start / per-customer state classification (architecture §2.9, ROADMAP Task 3.8).

Customers are classified deterministically:

- ``not_enough_data`` — near-zero tenure AND no usable behavioral features
  (or a zero-length observation window, which no duration model can fit).
- ``excluded`` — no usable predictors at all (nothing to model on), or an
  incomplete record missing some approved predictors (complete-case rule).
- ``scored`` — complete records with enough tenure to model.

``not_enough_data`` customers are never forced into Low/Medium/High tiers; they
carry no risk score.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from config.models import Node2Config
from node2.matrix import usable_predictors
from schemas.enums import CustomerState


def classify_customer(record: Any, predictors: Sequence[str], config: Node2Config) -> CustomerState:
    """Return the deterministic per-customer state (§2.9/§2.12)."""
    core = record.core_features.model_dump()
    usable = usable_predictors(core, predictors)

    if float(record.tenure) <= 0:
        return CustomerState.NOT_ENOUGH_DATA

    if len(usable) == 0:
        return CustomerState.EXCLUDED

    if len(usable) < len(predictors):
        # Incomplete record: near-zero tenure with too few usable features is a
        # cold-start; otherwise complete-case exclusion.
        if (
            float(record.tenure) <= config.cold_start_max_tenure_days
            and len(usable) < config.cold_start_min_behavioral_features
        ):
            return CustomerState.NOT_ENOUGH_DATA
        return CustomerState.EXCLUDED

    if (
        float(record.tenure) <= config.cold_start_max_tenure_days
        and len(usable) < config.cold_start_min_behavioral_features
    ):
        return CustomerState.NOT_ENOUGH_DATA

    return CustomerState.SCORED


def classify_customers(
    records: Sequence[Any], predictors: Sequence[str], config: Node2Config
) -> dict[str, CustomerState]:
    """Classify a batch; returns ``customer_id -> state``."""
    return {record.customer_id: classify_customer(record, predictors, config) for record in records}

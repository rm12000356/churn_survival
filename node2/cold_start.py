"""Cold-start / per-customer state classification (architecture §2.9, ROADMAP Task 3.8).

Customers are classified deterministically:

- ``not_enough_data`` — a zero-length observation window (which no duration model
  can fit), or tenure within ``cold_start_max_tenure_days`` **and** fewer than
  ``cold_start_min_behavioral_features`` usable predictors for that record
  (``cold_start_min_behavioral_features`` is compared against the count of
  non-``None`` approved ``core_features`` values for the record).
- ``excluded`` — no usable predictors at all (nothing to model on), or an
  incomplete record missing some approved predictors (complete-case rule).
- ``scored`` — complete records with enough tenure to model.

When *no* predictors are approved (``approved_core_keys == []``), the
feature-less exclusion does **not** apply: there are no features a record could
be "excluded for lack of", and the global Kaplan-Meier fallback still models
duration + event alone. Such records are ``scored`` (covered by the global
curve) unless the cold-start rule marks them ``not_enough_data``.

``not_enough_data`` customers are never forced into Low/Medium/High tiers; they
carry no risk score.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from config.models import Node2Config
from node2.matrix import modeling_values, usable_predictors
from schemas.enums import CustomerState


def classify_customer(record: Any, predictors: Sequence[str], config: Node2Config) -> CustomerState:
    """Return the deterministic per-customer state (§2.9/§2.12).

    ``cold_start_min_behavioral_features`` is compared against the number of
    usable predictors for this record (non-``None`` approved ``core_features``
    values); a record qualifies as cold-start only when it has fewer than that
    many usable predictors *and* its tenure is within
    ``cold_start_max_tenure_days``.

    When no predictors are approved (``predictors == []``), the no-features
    exclusion is skipped: ``len(usable) == 0`` is not "nothing to model on"
    because the global Kaplan-Meier fallback still models duration + event.
    A record with enough tenure is therefore ``scored``; the cold-start rule
    still applies.
    """
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
        # Incomplete record: complete-case exclusion (cold-start already handled above).
        return CustomerState.EXCLUDED

    return CustomerState.SCORED


def classify_customers(
    records: Sequence[Any], predictors: Sequence[str], config: Node2Config
) -> dict[str, CustomerState]:
    """Classify a batch; returns ``customer_id -> state``."""
    return {record.customer_id: classify_customer(record, predictors, config) for record in records}

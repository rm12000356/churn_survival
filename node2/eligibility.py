"""Model eligibility — hard gates + warning signals (architecture §2.4, ROADMAP Task 3.1).

Evaluated *before* any fit. A hard-gate failure blocks CoxPH and routes to the
Kaplan-Meier fallback; warning signals alone only downgrade the status to
WARNING. Gates are evaluated on the complete-case (scored) model matrix built by
``node2.matrix``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from config.models import Node2Config
from node2.matrix import (
    EARLY_EVENT_FRACTION,
    ELEVATED_MISSINGNESS_FRACTION,
    SHORT_FOLLOWUP_MEDIAN_DAYS,
    FeatureSpec,
    encode_categories,
    is_raw_column,
)
from node2.multicollinearity import multicollinearity_warnings


@dataclass(frozen=True)
class EligibilityResult:
    """Outcome of the pre-fit eligibility evaluation (§2.4)."""

    eligible: bool
    hard_failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _n_events(matrix: pd.DataFrame) -> int:
    return int(matrix["event"].sum())


def _check_variation(
    matrix: pd.DataFrame, specs: Sequence[FeatureSpec], config: Node2Config
) -> list[str]:
    """Every approved predictor must have meaningful variation (§2.4)."""
    failures: list[str] = []
    for spec in specs:
        if spec.kind == "numeric":
            column = spec.name
            values = matrix[column].astype(float)
            if len(values) == 0:
                continue
            most_common = float(values.mode().iloc[0])
            differing = float((values != most_common).mean())
            if differing < config.min_variation_fraction:
                failures.append(
                    f"predictor {spec.name!r} has no meaningful variation "
                    f"({differing:.1%} of rows differ from the modal value; "
                    f"threshold {config.min_variation_fraction:.1%})"
                )
        else:
            present_categories = [
                category
                for category in encode_categories(spec)
                if float(matrix[f"{spec.name}_{category}"].sum()) > 0
            ]
            if len(present_categories) < 1:
                failures.append(
                    f"categorical predictor {spec.name!r} has only its reference category "
                    f"({spec.categories[0]!r}) observed; variation requires >= 2 categories"
                )
    return failures


def check_eligibility(
    matrix: pd.DataFrame,
    specs: Sequence[FeatureSpec],
    config: Node2Config,
    *,
    n_input_records: int,
) -> EligibilityResult:
    """Evaluate hard gates + warning signals on the scored model matrix."""
    hard_failures: list[str] = []

    # 1. Survival columns present and correctly typed.
    if "duration" not in matrix.columns or "event" not in matrix.columns:
        hard_failures.append("missing survival columns 'duration'/'event' in model matrix")
    else:
        if not np.issubdtype(matrix["duration"].dtype, np.number):
            hard_failures.append("'duration' is not numeric")
        # 2. event_observed strictly binary.
        events = set(matrix["event"].unique())
        if not events.issubset({0, 1}):
            hard_failures.append(f"'event' must be strictly binary, got values {sorted(events)}")

    n_customers = int(len(matrix))
    n_events = _n_events(matrix) if "event" in matrix.columns else 0
    predictor_cols = [
        col
        for col in matrix.columns
        if col not in {"duration", "event"} and not is_raw_column(col)
    ]
    if not predictor_cols:
        hard_failures.append("no approved predictors; CoxPH ineligible")
    n_predictors = max(1, len(predictor_cols))

    # 3. Minimum number of customers.
    if n_customers < config.min_customers:
        hard_failures.append(
            f"min customers gate: {n_customers} < {config.min_customers}"
        )
    # 4. Minimum number of events.
    if n_events < config.min_events:
        hard_failures.append(f"min events gate: {n_events} < {config.min_events}")
    # 5. Events-to-predictors relationship.
    if n_events / n_predictors < config.min_events_per_predictor:
        hard_failures.append(
            f"events-per-predictor gate: {n_events} events / {n_predictors} predictors "
            f"= {n_events / n_predictors:.1f} < {config.min_events_per_predictor}"
        )
    # 6. Every predictor has meaningful variation.
    hard_failures.extend(_check_variation(matrix, specs, config))
    # 7. No catastrophic missing-data problem after encoding (complete-case exclusion).
    if n_input_records > 0:
        exclusion_fraction = 1 - (n_customers / n_input_records)
        if exclusion_fraction > config.missingness_threshold:
            hard_failures.append(
                f"missingness gate: {exclusion_fraction:.1%} of records excluded "
                f"(complete-case) exceeds {config.missingness_threshold:.1%}"
            )

    warnings: list[str] = []
    # Low absolute event count.
    if n_events < config.min_events * 2:
        warnings.append(f"low absolute event count: {n_events} events")
    # Elevated missingness.
    if n_input_records > 0:
        exclusion_fraction = 1 - (n_customers / n_input_records)
        if exclusion_fraction > ELEVATED_MISSINGNESS_FRACTION:
            warnings.append(
                "elevated missingness: "
                f"{exclusion_fraction:.1%} of records excluded (complete-case)"
            )
    # Short overall follow-up.
    if n_customers > 0 and float(matrix["duration"].median()) < SHORT_FOLLOWUP_MEDIAN_DAYS:
        warnings.append(
            f"short overall follow-up: median tenure {float(matrix['duration'].median()):.0f} days "
            f"< {SHORT_FOLLOWUP_MEDIAN_DAYS:.0f} days"
        )
    # Highly uneven event distribution over time (events clustered early).
    if n_events > 0:
        event_times = matrix.loc[matrix["event"] == 1, "duration"]
        max_duration = float(matrix["duration"].max()) or 1.0
        early_fraction = float((event_times < max_duration / 2).mean())
        if early_fraction > EARLY_EVENT_FRACTION:
            warnings.append(
                f"highly uneven event distribution: {early_fraction:.0%} of events occur "
                "in the first half of the follow-up range"
            )

    warnings.extend(multicollinearity_warnings(matrix, [], config))

    return EligibilityResult(
        eligible=not hard_failures,
        hard_failures=tuple(hard_failures),
        warnings=tuple(warnings),
    )

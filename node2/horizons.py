"""Survival-probability horizons (architecture §2.7, ROADMAP Task 3.6).

A horizon ``t`` is ``AVAILABLE`` only when the *fit data* can support it:

1. enough customers observed for at least ``t`` days,
2. enough events at/after ``t``,
3. the empirical survival estimate at ``t`` is not dominated by uncertainty
   (Greenwood CI width within the configured maximum).

Horizons are data-driven and configurable (§2.7: a B2B client may want 365d).
Never expose a horizon the data cannot support (§7).
"""

from __future__ import annotations

import pandas as pd
from lifelines import KaplanMeierFitter

from config.models import Node2Config
from schemas.enums import HorizonStatus


def _ci_bounds(ci: pd.DataFrame) -> tuple[float, float] | None:
    """Robustly read (lower, upper) from a lifelines KM confidence-interval frame."""
    if ci.empty:
        return None
    lower_col = next((c for c in ci.columns if "lower" in c), None)
    upper_col = next((c for c in ci.columns if "upper" in c), None)
    if lower_col is None or upper_col is None:
        return None
    return float(ci[lower_col].iloc[0]), float(ci[upper_col].iloc[0])


def km_ci_width(fit_data: pd.DataFrame, t: float) -> float | None:
    """Greenwood CI width of the empirical survival curve at time ``t``.

    Returns ``None`` when no events are observed (undefined uncertainty).
    """
    kmf = KaplanMeierFitter()
    kmf.fit(fit_data["duration"], event_observed=fit_data["event"], timeline=[t])
    bounds = _ci_bounds(kmf.confidence_interval_)
    if bounds is None:
        return None
    lower, upper = bounds
    return upper - lower


def horizon_statuses(
    fit_data: pd.DataFrame,
    config: Node2Config,
) -> dict[int, HorizonStatus]:
    """Availability status for every configured horizon (deterministic)."""
    durations = fit_data["duration"].astype(float)
    events = fit_data["event"].astype(int).to_numpy()
    statuses: dict[int, HorizonStatus] = {}
    for t in config.horizons:
        n_observed = int((durations >= t).sum())
        n_events_after = int((durations[events == 1] >= t).sum())
        width = km_ci_width(fit_data, float(t))
        available = (
            n_observed >= config.horizon_min_observed_customers
            and n_events_after >= config.horizon_min_events_around
            and width is not None
            and width <= config.horizon_max_ci_width
        )
        statuses[t] = HorizonStatus.AVAILABLE if available else HorizonStatus.INSUFFICIENT_DATA
    return statuses

"""Kaplan-Meier fallback path (architecture §2.8, ROADMAP Task 3.7).

- The global survival curve is *always* produced.
- Segment curves only when a pre-approved categorical feature exists and *every*
  segment meets the minimum customer and event counts — never create tiny, noisy
  segments.
- ``risk_scores`` is always null (KM does not produce individual Cox-style risk
  scores); segment curves are not individual risk scores.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

import pandas as pd
from lifelines import KaplanMeierFitter

from config.models import Node2Config
from node2.matrix import RAW_SUFFIX, FeatureSpec


@dataclass(frozen=True)
class KMResult:
    """Fitted KM fallback: global curve + optional segment curves."""

    global_curve: KaplanMeierFitter
    segment_feature: str | None = None
    segment_curves: dict[str, KaplanMeierFitter] = field(default_factory=dict)

    def curve_for(self, row: pd.Series) -> KaplanMeierFitter:
        if self.segment_feature is None:
            return self.global_curve
        category = row[self.segment_feature]
        return self.segment_curves.get(category, self.global_curve)


def _fit_curve(durations: pd.Series, events: pd.Series, timeline: list[float]) -> KaplanMeierFitter:
    kmf = KaplanMeierFitter()
    kmf.fit(durations, event_observed=events, timeline=timeline)
    return kmf


def _select_segment_feature(
    fit_data: pd.DataFrame, specs: Sequence[FeatureSpec], config: Node2Config
) -> str | None:
    """First categorical predictor whose *every* segment meets count/event mins."""
    for spec in specs:
        if spec.kind != "categorical":
            continue
        raw_col = f"{spec.name}{RAW_SUFFIX}"
        if raw_col not in fit_data.columns:
            continue
        segments_ok = True
        for category in spec.categories:
            subset = fit_data[fit_data[raw_col] == category]
            n_events = int((subset["event"] == 1).sum())
            if (
                len(subset) < config.km_segment_min_customers
                or n_events < config.km_segment_min_events
            ):
                segments_ok = False
                break
        if segments_ok:
            return raw_col
    return None


def fit_km(
    fit_data: pd.DataFrame,
    specs: Sequence[FeatureSpec],
    config: Node2Config,
    *,
    timeline: list[float],
) -> KMResult:
    """Fit the global KM curve, plus segment curves when supportable (§2.8)."""
    global_curve = _fit_curve(fit_data["duration"], fit_data["event"], timeline)
    segment_feature = _select_segment_feature(fit_data, specs, config)
    segment_curves: dict[str, KaplanMeierFitter] = {}
    if segment_feature is not None:
        for category in sorted(fit_data[segment_feature].dropna().unique()):
            subset = fit_data[fit_data[segment_feature] == category]
            segment_curves[str(category)] = _fit_curve(
                subset["duration"], subset["event"], timeline
            )
    return KMResult(
        global_curve=global_curve,
        segment_feature=segment_feature,
        segment_curves=segment_curves,
    )


def _ci_bounds(row: pd.Series) -> list[float]:
    """Read KM (lower, upper) confidence bounds from a lifelines 0.30 row."""
    lower = next((float(v) for c, v in row.items() if "lower" in c), None)
    upper = next((float(v) for c, v in row.items() if "upper" in c), None)
    lo = lower if lower is not None else float("nan")
    hi = upper if upper is not None else float("nan")
    return [lo, hi]


def survival_at_times(
    km: KMResult,
    matrix: pd.DataFrame,
    times: Sequence[float],
) -> dict[float, tuple[list[float], list[list[float]]]]:
    """Per-customer survival values + CIs at each horizon.

    Returns ``{t: (values, ci)}`` aligned to ``matrix`` row order. Values come
    from the customer's segment curve (or the global curve when no segments).
    """
    result: dict[float, tuple[list[float], list[list[float]]]] = {}
    for t in times:
        values: list[float] = []
        cis: list[list[float]] = []
        for _, row in matrix.iterrows():
            curve = km.curve_for(row)
            survival = float(curve.survival_function_.loc[t, "KM_estimate"])
            ci = curve.confidence_interval_.loc[t]
            values.append(survival)
            cis.append(_ci_bounds(ci))
        result[t] = (values, cis)
    return result

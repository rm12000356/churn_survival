from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from lifelines import KaplanMeierFitter

from config.models import Node2Config
from node2.matrix import RAW_SUFFIX, FeatureSpec


@dataclass(frozen=True)
class KMResult:
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
    lower = next((float(v) for c, v in row.items() if "lower" in c), None)
    upper = next((float(v) for c, v in row.items() if "upper" in c), None)
    lo = lower if lower is not None else float("nan")
    hi = upper if upper is not None else float("nan")
    return [lo, hi]


def window_terms(curve: KaplanMeierFitter, start: float, t: float) -> tuple[float, float | None]:
    table = curve.event_table
    times = table.index.to_numpy(dtype=float)
    mask = (times > start) & (times <= start + t)
    deaths = table["observed"].to_numpy(dtype=float)[mask]
    at_risk = table["at_risk"].to_numpy(dtype=float)[mask]
    keep = deaths > 0
    deaths, at_risk = deaths[keep], at_risk[keep]
    survival = float(np.prod(1.0 - deaths / at_risk)) if len(deaths) else 1.0
    if np.any(at_risk - deaths <= 0):
        return survival, None
    greenwood = float(np.sum(deaths / (at_risk * (at_risk - deaths))))
    return survival, greenwood


def loglog_ci(survival: float, greenwood: float | None) -> list[float | None]:
    if greenwood is None or not 0.0 < survival < 1.0 or greenwood <= 0.0:
        return [None, None]
    log_s = math.log(survival)
    spread = 1.96 * math.sqrt(greenwood) / abs(log_s)
    lower = survival ** math.exp(spread)
    upper = survival ** math.exp(-spread)
    return [float(lower), float(upper)]


def forward_survival(
    km: KMResult,
    matrix: pd.DataFrame,
    t: float,
) -> tuple[list[float], list[float | None]]:
    values: list[float] = []
    greenwood: list[float | None] = []
    cache: dict[tuple[int, float], tuple[float, float | None]] = {}
    for _, row in matrix.iterrows():
        curve = km.curve_for(row)
        start = float(row["duration"])
        key = (id(curve), start)
        if key not in cache:
            cache[key] = window_terms(curve, start, float(t))
        value, variance = cache[key]
        values.append(value)
        greenwood.append(variance)
    return values, greenwood


def survival_at_times(
    km: KMResult,
    matrix: pd.DataFrame,
    times: Sequence[float],
) -> dict[float, tuple[list[float], list[list[float]]]]:
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

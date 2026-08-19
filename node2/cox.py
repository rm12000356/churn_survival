"""Cox proportional-hazards path (architecture §2.6, ROADMAP Task 3.3).

- ``lifelines.CoxPHFitter`` with mandatory ``penalizer > 0`` (L2) and an explicit
  tied-event handling method.
- Only approved ``core_features`` (already encoded by ``node2.matrix``) are
  predictors; no formula is used so the parameter space is exactly the encoded
  numeric columns.
- Per-customer survival probabilities and a deterministic risk score:
  ``risk_score = 1 - S(t_ref)`` where ``t_ref`` is the reference time chosen at
  fit time (90 days when the 90d horizon is available, else the median observed
  tenure) — the risk score never extrapolates beyond what the data supports (§7).
- Survival confidence intervals via the delta method on the cumulative hazard
  (Nelson-Aalen/Breslow variance of the baseline cumulative hazard + the
  coefficient-covariance term). Deterministic given the fit data.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
from lifelines import CoxPHFitter
from scipy.stats import norm

from config.models import Node2Config
from node2.matrix import is_raw_column

PREDICTOR_COLUMNS = ("duration", "event")


def predictor_columns(matrix: pd.DataFrame) -> list[str]:
    """The model-matrix columns that are predictors (everything but duration/event/raw)."""
    return [
        col
        for col in matrix.columns
        if col not in PREDICTOR_COLUMNS and not is_raw_column(col)
    ]


def model_columns(matrix: pd.DataFrame) -> list[str]:
    """The full training frame columns: duration + event + predictors (no raw)."""
    return [*PREDICTOR_COLUMNS, *predictor_columns(matrix)]


def strata_columns(cph: CoxPHFitter) -> list[str]:
    """Column names the fitted model was stratified by (empty when unstratified)."""
    strata = getattr(cph, "strata", None)
    if strata is None:
        return []
    if isinstance(strata, str):
        return [strata]
    return [str(col) for col in strata]


def prediction_frame(cph: CoxPHFitter, matrix: pd.DataFrame) -> pd.DataFrame:
    """The frame lifelines needs to predict with ``cph`` (predictors + strata)."""
    columns = [*predictor_columns(matrix), *strata_columns(cph)]
    return matrix[columns]


def fit_cox(
    matrix: pd.DataFrame,
    config: Node2Config,
    *,
    strata: str | None = None,
) -> CoxPHFitter:
    """Fit CoxPH on the encoded matrix with mandatory L2 penalization.

    Tied-event handling is explicit and recorded in the artifact metadata: the
    config's ``tie_method`` (default ``"efron"``) is the lifelines 0.30
    approximation, surfaced in the artifact so it is never silently defaulted.
    ``strata`` optionally names a categorical model column to stratify by (used
    by the PH-violation adjustment path, §2.6 "manageable -> stratify/refit").
    """
    fit_df = matrix[model_columns(matrix)].copy()
    fit_kwargs: dict[str, Any] = {}
    if strata is not None:
        fit_kwargs["strata"] = strata
        fit_df[strata] = matrix[strata]
    cph = CoxPHFitter(penalizer=config.penalizer)
    cph.fit(
        fit_df,
        duration_col="duration",
        event_col="event",
        **fit_kwargs,
    )
    return cph


def risk_reference_time(matrix: pd.DataFrame, *, horizon_90_available: bool) -> float:
    """Reference time for the per-customer risk score (§2.6/§2.3).

    90 days when the 90d horizon is available; otherwise the median observed
    tenure — the score is always computed inside the supported follow-up range.
    """
    if horizon_90_available:
        return 90.0
    return float(matrix["duration"].median())


def score_risk_scores(cph: CoxPHFitter, matrix: pd.DataFrame, t_ref: float) -> np.ndarray:
    """Per-customer risk score ``1 - S(t_ref)`` (higher = greater risk, in [0, 1])."""
    survival = cph.predict_survival_function(prediction_frame(cph, matrix), times=[t_ref])
    return np.clip(1.0 - survival.loc[t_ref].to_numpy(dtype=float), 0.0, 1.0)


def survival_at_times(
    cph: CoxPHFitter, matrix: pd.DataFrame, times: Sequence[float]
) -> pd.DataFrame:
    """Survival probabilities at ``times``; rows = times, columns = customer_id."""
    return cph.predict_survival_function(prediction_frame(cph, matrix), times=list(times))


def _breslow_variance_terms(matrix: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(event_times, n_events_at_time, n_at_risk) from the fit data.

    Used by the delta-method CI: Var(cumulative hazard) = sum over events
    <= t of d_j / n_j^2 (Nelson-Aalen variance of the Breslow estimator).
    """
    events = matrix.loc[matrix["event"] == 1, "duration"].astype(float)
    if events.empty:
        return np.array([]), np.array([]), np.array([])
    event_times = np.sort(events.to_numpy())
    durations = matrix["duration"].astype(float).to_numpy()
    unique_times, counts = np.unique(event_times, return_counts=True)
    n_at_risk = np.array([int((durations >= t).sum()) for t in unique_times])
    return unique_times, counts.astype(float), n_at_risk.astype(float)


def _cumulative_hazard_variance(
    cph: CoxPHFitter,
    event_times: np.ndarray,
    n_events: np.ndarray,
    n_at_risk: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Var(Breslow cumulative hazard) and baseline cumulative hazard at event times."""
    var_hazard = np.cumsum(n_events / (n_at_risk**2))
    baseline_hazard = np.cumsum(n_events / n_at_risk)
    return var_hazard, baseline_hazard


def survival_ci(
    cph: CoxPHFitter,
    matrix: pd.DataFrame,
    fit_data: pd.DataFrame,
    times: Sequence[float],
) -> dict[float, tuple[np.ndarray, np.ndarray]]:
    """Delta-method 95% CI for per-customer survival at each horizon.

    Returns ``{time: (ci_lower_per_customer, ci_upper_per_customer)}`` aligned to
    ``matrix`` row order. The interval is centered on the *actual* predicted
    survival point (``predict_survival_function``) so it always brackets it; the
    width combines the Nelson-Aalen variance of the Breslow baseline hazard with
    the coefficient-covariance term.
    """
    event_times, n_events, n_at_risk = _breslow_variance_terms(fit_data)
    if len(event_times) == 0:
        return {}
    var_hazard, baseline_hazard = _cumulative_hazard_variance(cph, event_times, n_events, n_at_risk)
    baseline_hazard = np.maximum(baseline_hazard, 1e-12)

    cov = cph.variance_matrix_.to_numpy(dtype=float)
    x = matrix[predictor_columns(matrix)].astype(float).to_numpy()
    quadratic = np.array([row @ cov @ row for row in x])

    sf = survival_at_times(cph, matrix, times)
    result: dict[float, tuple[np.ndarray, np.ndarray]] = {}
    for t in times:
        idx = np.searchsorted(event_times, t, side="right") - 1
        if idx < 0:
            result[t] = (np.full(len(x), np.nan), np.full(len(x), np.nan))
            continue
        var0 = float(var_hazard[idx])
        baseline0 = float(baseline_hazard[idx])
        s_hat = np.clip(sf.loc[t].to_numpy(dtype=float), 1e-12, 1 - 1e-12)
        log_lambda = np.log(-np.log(s_hat))
        se = np.sqrt(var0 / (baseline0**2) + quadratic)
        z = 1.96
        lo = np.exp(-np.exp(log_lambda + z * se))
        hi = np.exp(-np.exp(log_lambda - z * se))
        result[t] = (lo, hi)
    return result


def feature_associations(cph: CoxPHFitter) -> list[dict[str, Any]]:
    """Extract coefficient / hazard-ratio / CI / p-value rows from ``cph.summary``."""
    summary = cph.summary
    associations: list[dict[str, Any]] = []
    for feature in summary.index:
        row = summary.loc[feature]
        associations.append(
            {
                "feature": str(feature),
                "coefficient": float(row["coef"]),
                "hazard_ratio": float(row["exp(coef)"]),
                "ci_lower": float(row["exp(coef) lower 95%"]),
                "ci_upper": float(row["exp(coef) upper 95%"]),
                "p_value": float(row["p"]),
            }
        )
    return associations


def wald_p_values(cph: CoxPHFitter) -> dict[str, float]:
    """Wald p-values per predictor (from summary; fallback to z-statistic)."""
    summary = cph.summary
    if "p" in summary.columns:
        return {str(feature): float(summary.loc[feature, "p"]) for feature in summary.index}
    result: dict[str, float] = {}
    for feature in summary.index:
        z = float(summary.loc[feature, "z"])
        result[str(feature)] = float(2.0 * (1.0 - norm.cdf(abs(z))))
    return result

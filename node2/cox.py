from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd
from lifelines import CoxPHFitter
from scipy.stats import norm

from config.models import Node2Config
from node2.matrix import RAW_SUFFIX, FeatureSpec, encode_categories, is_raw_column
from schemas.node2 import FeatureContribution

PREDICTOR_COLUMNS = ("duration", "event")


def predictor_columns(matrix: pd.DataFrame) -> list[str]:
    return [
        col
        for col in matrix.columns
        if col not in PREDICTOR_COLUMNS and not is_raw_column(col)
    ]


def model_columns(matrix: pd.DataFrame) -> list[str]:
    return [*PREDICTOR_COLUMNS, *predictor_columns(matrix)]


def strata_columns(cph: CoxPHFitter) -> list[str]:
    strata = getattr(cph, "strata", None)
    if strata is None:
        return []
    if isinstance(strata, str):
        return [strata]
    return [str(col) for col in strata]


def fitted_predictors(cph: CoxPHFitter) -> list[str]:
    return [str(col) for col in cph.params_.index]


def prediction_frame(cph: CoxPHFitter, matrix: pd.DataFrame) -> pd.DataFrame:
    return matrix[[*fitted_predictors(cph), *strata_columns(cph)]]


def training_frame(cph: CoxPHFitter, matrix: pd.DataFrame) -> pd.DataFrame:
    return matrix[[*PREDICTOR_COLUMNS, *fitted_predictors(cph), *strata_columns(cph)]]


def strata_dummy_columns(matrix: pd.DataFrame, strata: str) -> list[str]:
    base = strata[: -len(RAW_SUFFIX)] if strata.endswith(RAW_SUFFIX) else strata
    dummies = {f"{base}_{category}" for category in matrix[strata].dropna().unique()}
    return [col for col in predictor_columns(matrix) if col in dummies]


def fit_cox(
    matrix: pd.DataFrame,
    config: Node2Config,
    *,
    strata: str | None = None,
) -> CoxPHFitter:
    fit_df = matrix[model_columns(matrix)].copy()
    fit_kwargs: dict[str, Any] = {}
    if strata is not None:
        fit_kwargs["strata"] = strata
        fit_df = fit_df.drop(columns=strata_dummy_columns(matrix, strata))
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
    if horizon_90_available:
        return 90.0
    return float(matrix["duration"].median())


def score_risk_scores(cph: CoxPHFitter, matrix: pd.DataFrame, t_ref: float) -> np.ndarray:
    survival = cph.predict_survival_function(prediction_frame(cph, matrix), times=[t_ref])
    return np.clip(1.0 - survival.loc[t_ref].to_numpy(dtype=float), 0.0, 1.0)


def survival_at_times(
    cph: CoxPHFitter, matrix: pd.DataFrame, times: Sequence[float]
) -> pd.DataFrame:
    return cph.predict_survival_function(prediction_frame(cph, matrix), times=list(times))


def forward_survival(cph: CoxPHFitter, matrix: pd.DataFrame, t: float) -> np.ndarray:
    if len(matrix) == 0:
        return np.array([], dtype=float)
    frame = prediction_frame(cph, matrix).reset_index(drop=True)
    tenure = matrix["duration"].to_numpy(dtype=float)
    sf = cph.predict_survival_function(frame, times=[float(t)], conditional_after=tenure)
    row = sf.iloc[0].reindex(range(len(frame)))
    return row.to_numpy(dtype=float)


def _breslow_variance_terms(matrix: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
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
    var_hazard = np.cumsum(n_events / (n_at_risk**2))
    baseline_hazard = np.cumsum(n_events / n_at_risk)
    return var_hazard, baseline_hazard


def survival_ci(
    cph: CoxPHFitter,
    matrix: pd.DataFrame,
    fit_data: pd.DataFrame,
    times: Sequence[float],
) -> dict[float, tuple[np.ndarray, np.ndarray]]:
    event_times, n_events, n_at_risk = _breslow_variance_terms(fit_data)
    if len(event_times) == 0:
        blank = np.full(len(matrix), np.nan)
        return {t: (blank.copy(), blank.copy()) for t in times}
    var_hazard, baseline_hazard = _cumulative_hazard_variance(cph, event_times, n_events, n_at_risk)
    baseline_hazard = np.maximum(baseline_hazard, 1e-12)

    cov = cph.variance_matrix_.to_numpy(dtype=float)
    x = matrix[fitted_predictors(cph)].astype(float).to_numpy()
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


def _column_specs(
    specs: Sequence[FeatureSpec],
) -> dict[str, tuple[FeatureSpec, str | None]]:
    mapping: dict[str, tuple[FeatureSpec, str | None]] = {}
    for spec in specs:
        if spec.kind == "numeric":
            mapping[spec.name] = (spec, None)
        else:
            for category in encode_categories(spec):
                mapping[f"{spec.name}_{category}"] = (spec, category)
    return mapping


def feature_contributions(
    cph: CoxPHFitter,
    matrix: pd.DataFrame,
    fit_data: pd.DataFrame,
    specs: Sequence[FeatureSpec],
) -> tuple[float, list[float], list[list[FeatureContribution]]]:
    columns = _column_specs(specs)
    reliable = {
        item["feature"]: not (item["ci_lower"] <= 1.0 <= item["ci_upper"])
        for item in feature_associations(cph)
    }
    params = cph.params_
    numeric: list[tuple[str, FeatureSpec, float, float]] = []
    categorical: list[tuple[str, FeatureSpec, str, float]] = []
    for column in fitted_predictors(cph):
        if column not in columns:
            continue
        spec, category = columns[column]
        beta = float(params[column])
        if category is None:
            numeric.append((column, spec, beta, float(fit_data[column].astype(float).mean())))
        else:
            categorical.append((column, spec, category, beta))

    baseline = float(sum(beta * ref for _, _, beta, ref in numeric))
    relative: list[float] = []
    per_customer: list[list[FeatureContribution]] = []
    for row_id in matrix.index:
        records: list[FeatureContribution] = []
        for column, spec, beta, ref in numeric:
            raw = matrix.at[row_id, column]
            if raw is None or pd.isna(raw):
                continue
            value = float(raw)
            records.append(
                FeatureContribution(
                    feature=spec.name,
                    column=column,
                    kind="numeric",
                    value=value,
                    reference=ref,
                    coefficient=beta,
                    hazard_ratio=float(np.exp(beta)),
                    contribution=beta * (value - ref),
                    reliable=reliable.get(column, False),
                )
            )
        for column, spec, category, beta in categorical:
            if float(matrix.at[row_id, column]) != 1.0:
                continue
            records.append(
                FeatureContribution(
                    feature=spec.name,
                    column=column,
                    kind="categorical",
                    value=category,
                    reference=spec.categories[0] if spec.categories else None,
                    coefficient=beta,
                    hazard_ratio=float(np.exp(beta)),
                    contribution=beta,
                    reliable=reliable.get(column, False),
                )
            )
        relative.append(float(sum(record.contribution for record in records)))
        per_customer.append(records)
    return baseline, relative, per_customer


def wald_p_values(cph: CoxPHFitter) -> dict[str, float]:
    summary = cph.summary
    if "p" in summary.columns:
        return {str(feature): float(summary.loc[feature, "p"]) for feature in summary.index}
    result: dict[str, float] = {}
    for feature in summary.index:
        z = float(summary.loc[feature, "z"])
        result[str(feature)] = float(2.0 * (1.0 - norm.cdf(abs(z))))
    return result

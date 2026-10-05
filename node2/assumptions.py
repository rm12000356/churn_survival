from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from lifelines import CoxPHFitter
from lifelines.utils import concordance_index
from scipy import stats

from config.models import Node2Config
from node2.cox import fit_cox, prediction_frame, score_risk_scores, training_frame
from node2.matrix import RAW_SUFFIX, FeatureSpec


@dataclass(frozen=True)
class AssumptionResult:
    ph_p_values: dict[str, float]
    severity: str
    decision: str
    c_index: float | None
    c_index_ci: tuple[float, float] | None
    strata_used: str | None = None
    refitted_model: CoxPHFitter | None = None
    ph_p_values_after_refit: dict[str, float] | None = None
    severity_after_refit: str | None = None


def ph_test_p_values(cph: CoxPHFitter, matrix: pd.DataFrame) -> dict[str, float]:
    residuals = cph.compute_residuals(training_frame(cph, matrix), "scaled_schoenfeld")
    event_times = matrix.loc[residuals.index, "duration"].astype(float).to_numpy()
    times = stats.rankdata(event_times)
    p_values: dict[str, float] = {}
    for feature in residuals.columns:
        slope, _, _, p_value, _ = stats.linregress(times, residuals[feature].to_numpy(dtype=float))
        p_values[str(feature)] = float(p_value)
    return p_values


def decide_ph_severity(ph_p_values: dict[str, float], config: Node2Config) -> str:
    serious = [
        feature
        for feature, p in ph_p_values.items()
        if p < config.ph_p_value_serious
    ]
    if serious:
        return "serious"
    minor = [feature for feature, p in ph_p_values.items() if p < config.ph_p_value_warning]
    if minor:
        return "minor"
    return "none"


def attempt_stratified_refit(
    matrix: pd.DataFrame,
    specs: list[FeatureSpec],
    config: Node2Config,
    ph_p_values: dict[str, float],
) -> tuple[CoxPHFitter | None, str | None]:
    serious_features = [
        feature for feature, p in ph_p_values.items() if p < config.ph_p_value_serious
    ]
    worst = min(
        serious_features,
        key=lambda feature: ph_p_values[feature],
        default=None,
    )
    categorical_specs = [spec for spec in specs if spec.kind == "categorical"]
    strata = None
    for spec in categorical_specs:
        if worst is None:
            strata = f"{spec.name}{RAW_SUFFIX}"
            break
        if worst == spec.name or worst.startswith(f"{spec.name}_"):
            strata = f"{spec.name}{RAW_SUFFIX}"
            break
    if strata is None and categorical_specs:
        strata = f"{categorical_specs[0].name}{RAW_SUFFIX}"
    if strata is None or strata not in matrix.columns:
        return None, None
    strata_spec = next(
        spec for spec in specs if f"{spec.name}{RAW_SUFFIX}" == strata
    )
    if not any(spec.name != strata_spec.name for spec in specs):
        return None, None
    refitted = fit_cox(matrix, config, strata=strata)
    return refitted, strata


def bootstrap_c_index(
    cph: CoxPHFitter,
    matrix: pd.DataFrame,
    config: Node2Config,
    *,
    seed: int,
    t_ref: float | None = None,
) -> tuple[float, tuple[float, float]]:
    rng = np.random.default_rng(seed)
    if getattr(cph, "strata", None) and t_ref:
        risk = -score_risk_scores(cph, matrix, t_ref)
    else:
        risk = -cph.predict_partial_hazard(prediction_frame(cph, matrix)).to_numpy(dtype=float)
    durations = matrix["duration"].astype(float).to_numpy()
    events = matrix["event"].astype(int).to_numpy()
    n = len(matrix)
    point = float(concordance_index(durations, risk, events))
    if config.bootstrap_iterations <= 1:
        return point, (point, point)
    samples: list[float] = []
    for _ in range(config.bootstrap_iterations):
        idx = rng.integers(0, n, size=n)
        sample = concordance_index(durations[idx], risk[idx], events[idx])
        samples.append(float(sample))
    lo, hi = float(np.percentile(samples, 2.5)), float(np.percentile(samples, 97.5))
    return point, (lo, hi)


def run_assumptions(
    cph: CoxPHFitter,
    matrix: pd.DataFrame,
    specs: list[FeatureSpec],
    config: Node2Config,
    *,
    seed: int,
    t_ref: float | None = None,
) -> AssumptionResult:
    ph_p_values = ph_test_p_values(cph, matrix)
    severity = decide_ph_severity(ph_p_values, config)
    c_index, c_index_ci = bootstrap_c_index(cph, matrix, config, seed=seed, t_ref=t_ref)

    if severity == "serious":
        refitted, strata = attempt_stratified_refit(matrix, specs, config, ph_p_values)
        after: dict[str, float] | None = None
        severity_after: str | None = None
        if refitted is not None:
            after = ph_test_p_values(refitted, matrix)
            severity_after = decide_ph_severity(after, config)
            if severity_after != "serious":
                refit_c_index, refit_ci = bootstrap_c_index(
                    refitted, matrix, config, seed=seed, t_ref=t_ref
                )
                return AssumptionResult(
                    ph_p_values=ph_p_values,
                    severity=severity,
                    decision="stratify",
                    c_index=refit_c_index,
                    c_index_ci=refit_ci,
                    strata_used=strata,
                    refitted_model=refitted,
                    ph_p_values_after_refit=after,
                    severity_after_refit=severity_after,
                )
        return AssumptionResult(
            ph_p_values=ph_p_values,
            severity=severity,
            decision="fallback",
            c_index=c_index,
            c_index_ci=c_index_ci,
            strata_used=strata if refitted is not None else None,
            ph_p_values_after_refit=after,
            severity_after_refit=severity_after,
        )
    return AssumptionResult(
        ph_p_values=ph_p_values,
        severity=severity,
        decision="keep",
        c_index=c_index,
        c_index_ci=c_index_ci,
    )

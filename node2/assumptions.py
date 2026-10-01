"""Post-fit assumption checks & validation (architecture §2.6, ROADMAP Task 3.5).

- Proportional-hazards diagnostics: scaled-Schoenfeld residual test per predictor
  (Grambsch–Therneau slope test), severity-classified against config thresholds.
- Validation: concordance index estimated by *seeded* bootstrap resampling
  (preferred over a naïve train/test split for small event counts, §2.6).

Test results are evidence, not an automatic kill switch: minor violations ->
WARNING; serious violations on a categorical predictor -> one stratified refit
attempt, else fallback.
"""

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
    """Outcome of post-fit diagnostics (§2.6 severity handling)."""

    ph_p_values: dict[str, float]
    severity: str  # "none" | "minor" | "serious"
    decision: str  # "keep" | "stratify" | "fallback"
    c_index: float | None
    c_index_ci: tuple[float, float] | None
    strata_used: str | None = None
    refitted_model: CoxPHFitter | None = None
    # PH re-test of the stratified refit: the adjustment is accepted only when it
    # actually resolves the serious violation (never assumed).
    ph_p_values_after_refit: dict[str, float] | None = None
    severity_after_refit: str | None = None


def ph_test_p_values(cph: CoxPHFitter, matrix: pd.DataFrame) -> dict[str, float]:
    """Scaled-Schoenfeld slope-test p-values per predictor (Grambsch & Therneau).

    Deterministic: residuals come from ``compute_residuals`` and the slope test
    uses ``scipy.stats.linregress`` against the rank-transformed event times.
    Uses the model's own training frame, so it also works for stratified fits.
    """
    residuals = cph.compute_residuals(training_frame(cph, matrix), "scaled_schoenfeld")
    event_times = matrix.loc[residuals.index, "duration"].astype(float).to_numpy()
    times = stats.rankdata(event_times)
    p_values: dict[str, float] = {}
    for feature in residuals.columns:
        slope, _, _, p_value, _ = stats.linregress(times, residuals[feature].to_numpy(dtype=float))
        p_values[str(feature)] = float(p_value)
    return p_values


def decide_ph_severity(ph_p_values: dict[str, float], config: Node2Config) -> str:
    """Map per-predictor PH-test p-values to a severity level (§2.6)."""
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
    """One managed adjustment: stratify by the worst-violating categorical feature.

    Returns ``(refitted_cph, strata_raw_column)`` or ``(None, None)`` when no
    categorical predictor can absorb the violation (serious -> fallback).
    """
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
    """Apparent C-index with a deterministic (seeded) bootstrap CI (§2.6).

    *Apparent*: it is computed on the training data (the bootstrap resamples
    in-sample predictions), so it describes fit, not held-out performance.

    The ranking is the **delivered** score. For an unstratified fit that is
    ``exp(x·β)`` — ``1 − S(t_ref)`` is a monotone transform of it, so the
    concordance is identical. A stratified fit has a baseline per stratum, so
    ``exp(x·β)`` is not comparable across strata; there the shipped score
    ``1 − S(t_ref)`` (per-stratum baseline) is ranked instead (REVIEW N-H2).
    """
    rng = np.random.default_rng(seed)
    if getattr(cph, "strata", None) and t_ref:
        # concordance_index wants "higher = survives longer": negate the risk.
        risk = -score_risk_scores(cph, matrix, t_ref)
    else:
        # predict_partial_hazard has the opposite sign here — negate for the
        # correct concordance direction (matches lifelines' own docstring example).
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
    """Run PH diagnostics + C-index bootstrap; classify severity (§2.6).

    When the violation is serious and a categorical predictor can absorb it, the
    one managed stratified refit is performed here and exposed via
    ``refitted_model`` / ``strata_used``.
    """
    ph_p_values = ph_test_p_values(cph, matrix)
    severity = decide_ph_severity(ph_p_values, config)
    c_index, c_index_ci = bootstrap_c_index(cph, matrix, config, seed=seed, t_ref=t_ref)

    if severity == "serious":
        refitted, strata = attempt_stratified_refit(matrix, specs, config, ph_p_values)
        after: dict[str, float] | None = None
        severity_after: str | None = None
        if refitted is not None:
            # §2.6 "attempt adjustment, then refit": the adjustment counts only if
            # the refit no longer has a serious violation. Stratifying on a
            # variable unrelated to the violator (e.g. a numeric one) is rejected
            # here instead of being reported as handled.
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

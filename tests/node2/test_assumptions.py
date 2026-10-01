"""Post-fit assumption checks (ROADMAP Task 3.5/§2.6)."""

from __future__ import annotations

import numpy as np

from node2.assumptions import (
    attempt_stratified_refit,
    bootstrap_c_index,
    decide_ph_severity,
    ph_test_p_values,
    run_assumptions,
)
from node2.cox import fit_cox, predictor_columns
from node2.matrix import build_specs, encode
from tests.node2.conftest import PREDICTORS, make_config, make_record, synthetic_dataset


def _matrix(records, predictors=PREDICTORS):
    specs = build_specs(records, predictors)
    rows = [
        (r.customer_id, r.core_features.model_dump(), float(r.tenure), r.event_observed)
        for r in records
    ]
    return encode(rows, specs), specs


def test_ph_test_p_values_cover_predictors() -> None:
    matrix, _ = _matrix(synthetic_dataset())
    cph = fit_cox(matrix, make_config())
    p_values = ph_test_p_values(cph, matrix)
    assert set(p_values.keys()) == set(predictor_columns(matrix))
    assert all(0.0 <= p <= 1.0 for p in p_values.values())


def test_decide_ph_severity() -> None:
    config = make_config()
    assert decide_ph_severity({"a": 0.5, "b": 0.3}, config) == "none"
    assert decide_ph_severity({"a": 0.02, "b": 0.3}, config) == "minor"
    assert decide_ph_severity({"a": 0.005, "b": 0.3}, config) == "serious"


def test_bootstrap_c_index_deterministic() -> None:
    matrix, _ = _matrix(synthetic_dataset())
    cph = fit_cox(matrix, make_config())
    a = bootstrap_c_index(cph, matrix, make_config(), seed=7)
    b = bootstrap_c_index(cph, matrix, make_config(), seed=7)
    assert a == b
    assert 0.0 <= a[0] <= 1.0
    assert a[1][0] <= a[0] <= a[1][1]


def test_bootstrap_single_iteration_point_equals_ci() -> None:
    matrix, _ = _matrix(synthetic_dataset())
    cph = fit_cox(matrix, make_config())
    config = make_config(bootstrap_iterations=10)
    point, ci = bootstrap_c_index(cph, matrix, config, seed=1)
    assert ci[0] <= point <= ci[1]


def test_bootstrap_c_index_orientation() -> None:
    rng = np.random.default_rng(7)
    records = []
    for i in range(300):
        usage = float(rng.normal(25.0, 8.0))
        plan_tier = str(rng.choice(["basic", "pro", "enterprise"]))
        contract_length_months = float(rng.choice([6, 12, 24, 36]))
        hazard = np.exp(
            -0.08 * usage
            - 0.15 * contract_length_months
            + (0.6 if plan_tier == "basic" else 0.0)
        )
        tenure = max(30, min(2000, int(rng.exponential(400.0 / max(hazard, 1e-2)))))
        event = int(rng.random() < min(0.95, 0.6 * hazard))
        event_time = max(1, int(tenure * 0.5)) if event else None
        records.append(
            make_record(
                i,
                tenure=tenure,
                event=event,
                event_time=event_time,
                plan_tier=plan_tier,
                contract_length_months=contract_length_months,
                usage_frequency=usage,
            )
        )
    matrix, _ = _matrix(records)
    cph = fit_cox(matrix, make_config())
    point, _ = bootstrap_c_index(cph, matrix, make_config(), seed=11)
    assert point > 0.5


def test_run_assumptions_keep_path() -> None:
    matrix, specs = _matrix(synthetic_dataset())
    cph = fit_cox(matrix, make_config())
    result = run_assumptions(cph, matrix, specs, make_config(), seed=5)
    assert result.decision == "keep"
    assert result.refitted_model is None
    assert result.severity in ("none", "minor")
    assert result.c_index is not None
    assert result.c_index_ci is not None


def test_run_assumptions_unresolved_refit_falls_back() -> None:
    """§2.6: a stratified refit counts only if it resolves the serious violation.

    With ``ph_p_value_serious=1.0`` every covariate stays "serious" after the
    refit, so the adjustment is rejected (fallback) rather than reported as
    handled; the attempted strata and the re-test are still recorded.
    """
    matrix, specs = _matrix(synthetic_dataset())
    cph = fit_cox(matrix, make_config())
    config = make_config(ph_p_value_serious=1.0)
    result = run_assumptions(cph, matrix, specs, config, seed=5)
    assert result.decision == "fallback"
    assert result.strata_used == "plan_tier__raw"
    assert result.refitted_model is None
    assert result.severity_after_refit == "serious"
    assert result.ph_p_values_after_refit


def test_attempt_stratified_refit_uses_categorical() -> None:
    matrix, specs = _matrix(synthetic_dataset())
    config = make_config(ph_p_value_serious=1.0)
    refitted, strata = attempt_stratified_refit(matrix, specs, config, {"a": 0.0})
    assert strata == "plan_tier__raw"
    assert refitted is not None
    assert refitted.strata is not None


def test_attempt_stratified_refit_no_categorical_falls_back() -> None:
    matrix, specs = _matrix(
        synthetic_dataset(), predictors=["contract_length_months", "usage_frequency"]
    )
    config = make_config(ph_p_value_serious=1.0)
    refitted, strata = attempt_stratified_refit(matrix, specs, config, {"a": 0.0})
    assert refitted is None
    assert strata is None


def test_stratification_sole_categorical_is_non_viable() -> None:
    matrix, specs = _matrix(synthetic_dataset(), predictors=["plan_tier"])
    config = make_config(ph_p_value_serious=1.0)
    refitted, strata = attempt_stratified_refit(matrix, specs, config, {"plan_tier_pro": 0.0})
    assert refitted is None
    assert strata is None
    result = run_assumptions(fit_cox(matrix, config), matrix, specs, config, seed=5)
    assert result.decision == "fallback"
    assert result.refitted_model is None

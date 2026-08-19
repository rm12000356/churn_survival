"""Post-fit assumption checks (ROADMAP Task 3.5/§2.6)."""

from __future__ import annotations

from node2.assumptions import (
    attempt_stratified_refit,
    bootstrap_c_index,
    decide_ph_severity,
    ph_test_p_values,
    run_assumptions,
)
from node2.cox import fit_cox, predictor_columns
from node2.matrix import build_specs, encode
from tests.node2.conftest import PREDICTORS, make_config, synthetic_dataset


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


def test_run_assumptions_keep_path() -> None:
    matrix, specs = _matrix(synthetic_dataset())
    cph = fit_cox(matrix, make_config())
    result = run_assumptions(cph, matrix, specs, make_config(), seed=5)
    assert result.decision == "keep"
    assert result.refitted_model is None
    assert result.severity in ("none", "minor")
    assert result.c_index is not None
    assert result.c_index_ci is not None


def test_run_assumptions_stratify_on_serious_threshold() -> None:
    matrix, specs = _matrix(synthetic_dataset())
    cph = fit_cox(matrix, make_config())
    config = make_config(ph_p_value_serious=1.0)
    result = run_assumptions(cph, matrix, specs, config, seed=5)
    assert result.decision == "stratify"
    assert result.strata_used == "plan_tier__raw"
    assert result.refitted_model is not None


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

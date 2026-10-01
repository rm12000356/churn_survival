"""CoxPH fit / risk score / survival CI / associations (ROADMAP Task 3.3/§2.6)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from node2.cox import (
    PREDICTOR_COLUMNS,
    feature_associations,
    fit_cox,
    model_columns,
    predictor_columns,
    risk_reference_time,
    score_risk_scores,
    survival_at_times,
    survival_ci,
    wald_p_values,
)
from node2.matrix import build_specs, encode
from tests.node2.conftest import PREDICTORS, make_config, synthetic_dataset


def _matrix(records, predictors=PREDICTORS):
    specs = build_specs(records, predictors)
    rows = [
        (r.customer_id, r.core_features.model_dump(), float(r.tenure), r.event_observed)
        for r in records
    ]
    return encode(rows, specs), specs


def test_fit_cox_produces_model() -> None:
    matrix, _ = _matrix(synthetic_dataset())
    cph = fit_cox(matrix, make_config())
    assert cph.params_ is not None
    assert len(cph.params_) == 4  # contract + usage + 2 one-hots
    assert cph.penalizer == 0.1


def test_predictor_columns_exclude_duration_event_raw() -> None:
    matrix, _ = _matrix(synthetic_dataset())
    cols = predictor_columns(matrix)
    assert "duration" not in cols and "event" not in cols
    assert not any(c.endswith("__raw") for c in cols)
    assert set(cols) == {
        "contract_length_months",
        "usage_frequency",
        "plan_tier_enterprise",
        "plan_tier_pro",
    }
    assert model_columns(matrix)[:2] == list(PREDICTOR_COLUMNS)


def test_risk_reference_time_prefers_90d_when_available() -> None:
    matrix, _ = _matrix(synthetic_dataset())
    assert risk_reference_time(matrix, horizon_90_available=True) == 90.0
    assert (
        risk_reference_time(matrix, horizon_90_available=False)
        == float(matrix["duration"].median())
    )


def test_risk_scores_in_unit_interval_and_monotone() -> None:
    matrix, _ = _matrix(synthetic_dataset())
    cph = fit_cox(matrix, make_config())
    scores = score_risk_scores(cph, matrix, 90.0)
    assert len(scores) == len(matrix)
    assert np.all(scores >= 0.0) and np.all(scores <= 1.0)
    # Higher partial hazard -> lower survival -> higher risk score.
    hazard = cph.predict_partial_hazard(matrix[predictor_columns(matrix)]).to_numpy(dtype=float)
    order = np.argsort(hazard)
    assert np.all(np.diff(scores[order]) >= -1e-9)


def test_risk_scores_deterministic() -> None:
    matrix, _ = _matrix(synthetic_dataset())
    cph = fit_cox(matrix, make_config())
    a = score_risk_scores(cph, matrix, 90.0)
    b = score_risk_scores(cph, matrix, 90.0)
    np.testing.assert_array_equal(a, b)


def test_survival_at_times_frame_shape() -> None:
    matrix, _ = _matrix(synthetic_dataset())
    cph = fit_cox(matrix, make_config())
    sf = survival_at_times(cph, matrix, [30.0, 90.0, 180.0])
    assert list(sf.index) == [30.0, 90.0, 180.0]
    assert list(sf.columns) == list(matrix.index)
    assert np.all(sf.to_numpy() > 0)


def test_survival_ci_brackets_point_estimate() -> None:
    matrix, _ = _matrix(synthetic_dataset())
    cph = fit_cox(matrix, make_config())
    sf = survival_at_times(cph, matrix, [90.0])
    ci = survival_ci(cph, matrix, matrix, [90.0])
    assert set(ci.keys()) == {90.0}
    lo, hi = ci[90.0]
    assert len(lo) == len(matrix) == len(hi)
    point = sf.loc[90.0].to_numpy(dtype=float)
    assert np.all(lo <= hi + 1e-12)
    assert np.all(lo <= point + 1e-9)
    assert np.all(point <= hi + 1e-9)


def test_feature_associations_match_coefficients() -> None:
    matrix, _ = _matrix(synthetic_dataset())
    cph = fit_cox(matrix, make_config())
    associations = feature_associations(cph)
    assert {a["feature"] for a in associations} == set(predictor_columns(matrix))
    for a in associations:
        assert a["hazard_ratio"] == pytest.approx(math.exp(a["coefficient"]))
        assert a["ci_lower"] <= a["hazard_ratio"] <= a["ci_upper"]


def test_wald_p_values_present() -> None:
    matrix, _ = _matrix(synthetic_dataset())
    cph = fit_cox(matrix, make_config())
    p_values = wald_p_values(cph)
    assert set(p_values.keys()) == set(predictor_columns(matrix))
    assert all(0.0 <= p <= 1.0 for p in p_values.values())


def test_survival_ci_before_first_event_is_nan() -> None:
    matrix, _ = _matrix(synthetic_dataset())
    cph = fit_cox(matrix, make_config())
    ci = survival_ci(cph, matrix, matrix, [0.5])  # before any observed event time
    lo, hi = ci[0.5]
    assert np.all(np.isnan(lo)) and np.all(np.isnan(hi))


def test_survival_ci_no_events_is_all_nan_per_horizon() -> None:
    """Every requested horizon gets an entry; with no events it is all-NaN."""
    matrix, _ = _matrix(synthetic_dataset())
    cph = fit_cox(matrix, make_config())
    fit_data = matrix.copy()
    fit_data["event"] = 0
    ci = survival_ci(cph, matrix, fit_data, [90.0])
    assert set(ci) == {90.0}
    lo, hi = ci[90.0]
    assert len(lo) == len(matrix)
    assert np.all(np.isnan(lo)) and np.all(np.isnan(hi))

"""Kaplan-Meier fallback (ROADMAP Task 3.7/§2.8)."""

from __future__ import annotations

import pandas as pd

from node2.kaplan_meier import fit_km, survival_at_times
from node2.matrix import build_specs, encode
from tests.node2.conftest import PREDICTORS, make_config, synthetic_dataset


def _matrix(records, predictors=PREDICTORS):
    specs = build_specs(records, predictors)
    rows = [
        (r.customer_id, r.core_features.model_dump(), float(r.tenure), r.event_observed)
        for r in records
    ]
    return encode(rows, specs), specs


def test_global_curve_always_produced() -> None:
    matrix, specs = _matrix(synthetic_dataset())
    km = fit_km(matrix, specs, make_config(), timeline=[30.0, 90.0, 180.0])
    assert km.global_curve is not None
    assert set(km.global_curve.timeline) == {30.0, 90.0, 180.0}


def test_segments_only_when_every_segment_supported() -> None:
    matrix, specs = _matrix(synthetic_dataset())
    km = fit_km(matrix, specs, make_config(), timeline=[90.0])
    assert km.segment_feature == "plan_tier__raw"
    assert set(km.segment_curves.keys()) == {"basic", "enterprise", "pro"}


def test_tiny_segments_are_not_created() -> None:
    matrix, specs = _matrix(synthetic_dataset())
    config = make_config(km_segment_min_customers=10**6)
    km = fit_km(matrix, specs, config, timeline=[90.0])
    assert km.segment_feature is None
    assert km.segment_curves == {}


def test_curve_for_falls_back_to_global() -> None:
    matrix, specs = _matrix(synthetic_dataset())
    km = fit_km(matrix, specs, make_config(), timeline=[90.0])
    row = pd.Series({"plan_tier__raw": "gold"})
    assert km.curve_for(row) is km.global_curve
    row_known = pd.Series({"plan_tier__raw": "pro"})
    assert km.curve_for(row_known) is km.segment_curves["pro"]


def test_survival_at_times_aligned_to_matrix() -> None:
    matrix, specs = _matrix(synthetic_dataset())
    km = fit_km(matrix, specs, make_config(), timeline=[30.0, 90.0, 180.0])
    result = survival_at_times(km, matrix, [30.0, 90.0])
    assert set(result.keys()) == {30.0, 90.0}
    values, cis = result[90.0]
    assert len(values) == len(matrix)
    assert len(cis) == len(matrix)
    assert all(0.0 <= v <= 1.0 for v in values)
    for lo, hi in cis:
        assert lo <= hi + 1e-12

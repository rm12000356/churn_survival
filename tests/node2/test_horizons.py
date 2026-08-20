"""Horizon availability (ROADMAP Task 3.6/§2.7)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from node2.horizons import horizon_statuses, km_ci_width
from node2.matrix import build_specs, encode
from schemas.enums import HorizonStatus
from tests.node2.conftest import PREDICTORS, make_config, synthetic_dataset


def _matrix(records, predictors=PREDICTORS):
    specs = build_specs(records, predictors)
    rows = [
        (r.customer_id, r.core_features.model_dump(), float(r.tenure), r.event_observed)
        for r in records
    ]
    return encode(rows, specs), specs


def test_supported_dataset_yields_available_horizons() -> None:
    matrix, _ = _matrix(synthetic_dataset())
    statuses = horizon_statuses(matrix, make_config())
    assert set(statuses.keys()) == {30, 90, 180}
    assert statuses[30] == HorizonStatus.AVAILABLE
    assert statuses[90] == HorizonStatus.AVAILABLE
    assert statuses[180] == HorizonStatus.AVAILABLE


def test_tiny_dataset_is_insufficient_data() -> None:
    fit_data = pd.DataFrame(
        {"duration": [10.0, 20.0, 5.0], "event": [1, 1, 0]},
        index=["a", "b", "c"],
    )
    statuses = horizon_statuses(fit_data, make_config())
    assert all(status == HorizonStatus.INSUFFICIENT_DATA for status in statuses.values())


def test_horizon_min_observed_gate() -> None:
    matrix, _ = _matrix(synthetic_dataset())
    config = make_config(horizon_min_observed_customers=10**9)
    statuses = horizon_statuses(matrix, config)
    assert all(status == HorizonStatus.INSUFFICIENT_DATA for status in statuses.values())


def test_horizon_min_events_gate() -> None:
    matrix, _ = _matrix(synthetic_dataset())
    config = make_config(horizon_min_events_around=10**9)
    statuses = horizon_statuses(matrix, config)
    assert all(status == HorizonStatus.INSUFFICIENT_DATA for status in statuses.values())


def test_km_ci_width_is_finite_with_events() -> None:
    fit_data = pd.DataFrame(
        {"duration": [10.0, 20.0, 30.0, 40.0], "event": [1, 1, 1, 1]},
        index=["a", "b", "c", "d"],
    )
    width = km_ci_width(fit_data, 10.0)
    assert width is not None
    assert width >= 0.0


def test_km_ci_width_none_zero_or_nan_without_events() -> None:
    fit_data = pd.DataFrame(
        {"duration": [10.0, 20.0, 30.0], "event": [0, 0, 0]},
        index=["a", "b", "c"],
    )
    width = km_ci_width(fit_data, 10.0)
    assert width is None or width == 0.0 or np.isnan(width)

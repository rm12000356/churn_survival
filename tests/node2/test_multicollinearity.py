"""Multicollinearity warnings (ROADMAP Task 3.4/§2.5)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from node2.matrix import FeatureSpec
from node2.multicollinearity import multicollinearity_warnings
from tests.node2.conftest import make_config


def _matrix(columns: dict[str, list[float]]) -> pd.DataFrame:
    return pd.DataFrame(columns, index=[f"c{i}" for i in range(len(next(iter(columns.values()))))])


def test_no_warnings_for_independent_predictors() -> None:
    rng = np.random.default_rng(0)
    n = 100
    x = rng.normal(size=n)
    y = rng.normal(size=n)
    z = rng.normal(size=n)
    matrix = _matrix({"a": x, "b": y, "c": z})
    assert multicollinearity_warnings(matrix, [], make_config()) == []


def test_strong_correlation_warns() -> None:
    x = np.linspace(0, 1, 50)
    matrix = _matrix({"a": x, "b": 2.0 * x + 0.01})
    warnings = multicollinearity_warnings(matrix, [], make_config())
    assert any("strong pairwise correlation" in w for w in warnings)
    assert any("'a'" in w and "'b'" in w for w in warnings)


def test_high_vif_warns_when_enough_predictors() -> None:
    rng = np.random.default_rng(0)
    n = 100
    x = rng.normal(size=n)
    a = x + rng.normal(scale=0.01, size=n)
    b = x + rng.normal(scale=0.01, size=n)
    matrix = _matrix({"x": x, "a": a, "b": b})
    warnings = multicollinearity_warnings(matrix, [], make_config())
    assert any("high VIF" in w for w in warnings)


def test_no_config_means_no_warnings() -> None:
    matrix = _matrix({"a": [1.0, 2.0], "b": [1.0, 2.0]})
    assert multicollinearity_warnings(matrix, [], None) == []


def test_single_column_never_warns() -> None:
    matrix = _matrix({"a": [1.0, 2.0, 3.0]})
    assert multicollinearity_warnings(matrix, [], make_config()) == []


def test_predictor_filter_applies() -> None:
    x = np.linspace(0, 1, 50)
    matrix = _matrix({"a": x, "b": 2.0 * x + 0.01, "c": [1.0] * 50})
    specs = [FeatureSpec(name="a", kind="numeric"), FeatureSpec(name="b", kind="numeric")]
    warnings = multicollinearity_warnings(matrix, specs, make_config())
    assert any("'a'" in w and "'b'" in w for w in warnings)
    assert not any("'c'" in w for w in warnings)

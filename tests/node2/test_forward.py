"""Forward (conditional) survival — §2.12 amendment 2026-10-01 (phase 10, D-R1).

``S(T + t) / S(T)`` for each active scored customer at current tenure ``T``;
``None`` for churned customers and for windows past the longest observed tenure.
"""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from lifelines import CoxPHFitter, KaplanMeierFitter

from config.loader import load_node2_config
from node2.cox import forward_survival as cox_forward_survival
from node2.kaplan_meier import KMResult, loglog_ci, window_terms
from node2.kaplan_meier import forward_survival as km_forward_survival
from node2.node import fit_model, run_node2, score_to_output
from schemas.enums import CustomerState, ModelType
from schemas.node2 import Node2Output
from tests.node2.conftest import PREDICTORS, synthetic_dataset

NOW = datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)
DURATIONS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
EVENTS = [0, 1, 0, 1, 0, 1, 0, 1, 0, 0]


def _hand_curve() -> KaplanMeierFitter:
    """Events at 2, 4, 6, 8; fitted on a 2-point timeline like ``fit_km``."""
    kmf = KaplanMeierFitter()
    kmf.fit(DURATIONS, event_observed=EVENTS, timeline=[30.0, 90.0])
    return kmf


def test_window_terms_matches_hand_product() -> None:
    curve = _hand_curve()
    # Window (3, 8]: events at 4 (n=7), 6 (n=5), 8 (n=3).
    expected = (1 - 1 / 7) * (1 - 1 / 5) * (1 - 1 / 3)
    greenwood_expected = 1 / (7 * 6) + 1 / (5 * 4) + 1 / (3 * 2)
    value, greenwood = window_terms(curve, 3.0, 5.0)
    assert value == pytest.approx(expected)
    assert greenwood == pytest.approx(greenwood_expected)


def test_window_terms_uses_full_table_not_timeline() -> None:
    curve = _hand_curve()
    assert list(curve.survival_function_.index) == [30.0, 90.0]
    value, _ = window_terms(curve, 0.0, 10.0)
    full = KaplanMeierFitter().fit(DURATIONS, event_observed=EVENTS)
    assert value == pytest.approx(float(full.survival_function_.loc[10.0].iloc[0]))


def test_window_without_events_is_one_with_no_band() -> None:
    value, greenwood = window_terms(_hand_curve(), 8.0, 2.0)
    assert value == 1.0
    assert greenwood == 0.0
    assert loglog_ci(value, greenwood) == [None, None]


def test_loglog_ci_brackets_point_and_rejects_undefined() -> None:
    lower, upper = loglog_ci(0.8, 0.01)
    assert lower is not None and upper is not None
    assert 0.0 < lower < 0.8 < upper < 1.0
    assert loglog_ci(0.8, None) == [None, None]
    assert loglog_ci(0.0, 0.01) == [None, None]


def test_km_forward_reuses_curve_windows() -> None:
    curve = _hand_curve()
    km = KMResult(global_curve=curve)
    matrix = pd.DataFrame({"duration": [3.0, 3.0, 0.0], "event": [0, 0, 0]}, index=["a", "b", "c"])
    values, greenwood = km_forward_survival(km, matrix, 5.0)
    assert values[0] == values[1] == pytest.approx(window_terms(curve, 3.0, 5.0)[0])
    assert values[2] == pytest.approx(window_terms(curve, 0.0, 5.0)[0])
    assert len(greenwood) == 3


@pytest.mark.parametrize("stratified", [False, True])
def test_cox_forward_equals_survival_ratio(stratified: bool) -> None:
    rng = np.random.default_rng(7)
    n = 300
    frame = pd.DataFrame(
        {
            "duration": rng.integers(5, 400, n).astype(float),
            "event": rng.integers(0, 2, n),
            "x": rng.normal(size=n),
            "g__raw": rng.choice(["a", "b"], n),
        },
        index=[f"c{i:03d}" for i in range(n)],
    )
    fit_df = frame[["duration", "event", "x"]].copy()
    kwargs = {}
    if stratified:
        fit_df["g__raw"] = frame["g__raw"]
        kwargs["strata"] = "g__raw"
    cph = CoxPHFitter(penalizer=0.1).fit(fit_df, "duration", "event", **kwargs)
    sample = frame.iloc[:40]
    forward = cox_forward_survival(cph, sample, 30.0)
    predictors = ["x", "g__raw"] if stratified else ["x"]
    for i, (_, row) in enumerate(sample.iterrows()):
        one = pd.DataFrame([row[predictors]])
        start = float(row["duration"])
        sf = cph.predict_survival_function(one, times=[start, start + 30.0])
        expected = float(sf.iloc[1, 0] / sf.iloc[0, 0])
        assert forward[i] == pytest.approx(expected, rel=1e-9), (i, stratified)


def test_run_emits_forward_tenure_event_aligned() -> None:
    output = run_node2(synthetic_dataset(), load_node2_config("1"), PREDICTORS, now=NOW)
    assert output.model_type == ModelType.COX_PH
    assert output.customer_tenure_days is not None
    assert output.customer_event_observed is not None
    assert output.forward_survival is not None
    assert output.max_follow_up_days is not None
    assert len(output.customer_tenure_days) == len(output.customer_ids)
    assert len(output.customer_event_observed) == len(output.customer_ids)
    forward = output.forward_survival["90d"]
    assert forward.ci_approximate is True
    scored = [
        i for i, state in enumerate(output.customer_states) if state == CustomerState.SCORED
    ]
    assert len(forward.values) == len(forward.ci) == len(scored)

    for position, index in enumerate(scored):
        value = forward.values[position]
        churned = output.customer_event_observed[index] == 1
        beyond = output.customer_tenure_days[index] + 90.0 > output.max_follow_up_days
        if churned or beyond:
            assert value is None
            assert forward.ci[position] == [None, None]
        else:
            assert value is not None and 0.0 <= value <= 1.0
            lower, upper = forward.ci[position]
            if lower is not None and upper is not None:
                assert lower <= value <= upper
    assert any(v is not None for v in forward.values)
    assert any(v is None for v in forward.values)


def test_km_path_forward_not_approximate() -> None:
    output = run_node2(synthetic_dataset(), load_node2_config("1"), [], now=NOW)
    assert output.model_type == ModelType.KAPLAN_MEIER
    assert output.forward_survival is not None
    assert output.forward_survival["90d"].ci_approximate is False


def test_forward_output_is_deterministic_and_json_safe() -> None:
    records = synthetic_dataset()
    artifact = fit_model(records, load_node2_config("1"), PREDICTORS, now=NOW)
    first = score_to_output(artifact, records).model_dump_json()
    second = score_to_output(artifact, records).model_dump_json()
    assert first == second
    assert "NaN" not in first and "Infinity" not in first


def test_old_output_without_forward_fields_validates() -> None:
    output = Node2Output.model_validate(
        {
            "model_type": "kaplan_meier",
            "model_status": "FALLBACK",
            "model_version": "abc",
            "survival_probabilities": {},
        }
    )
    assert output.forward_survival is None
    assert output.customer_tenure_days is None

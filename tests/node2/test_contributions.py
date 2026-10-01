"""Per-customer contributions to relative log-hazard (architecture §2.12b, 2026-10-01).

``contribution_ij = β_j · (x_ij − ref_j)`` against the model reference profile
(numerics at their training mean, categoricals at the reference category);
``LP_i = baseline_log_hazard + relative_log_hazard_i`` exactly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pandas as pd
import pytest
from lifelines import CoxPHFitter

from config.loader import load_node2_config
from node2.cox import feature_associations, feature_contributions, fit_cox, fitted_predictors
from node2.node import _prepare, fit_model, run_node2, score_to_output
from schemas.enums import CustomerState, ModelType
from schemas.node2 import Node2Output
from tests.node2.conftest import PREDICTORS, synthetic_dataset

NOW = datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def fitted() -> tuple[Any, ...]:
    records = synthetic_dataset()
    config = load_node2_config("1")
    _, _, specs, matrix = _prepare(records, PREDICTORS, config)
    cph = fit_cox(matrix, config)
    baseline, relative, contributions = feature_contributions(cph, matrix, matrix, specs)
    return cph, matrix, specs, baseline, relative, contributions


def _linear_predictor(cph: CoxPHFitter, matrix: pd.DataFrame, row_id: str) -> float:
    """LP recomputed independently from the fitted β and the encoded row."""
    return float(
        sum(
            float(cph.params_[col]) * float(matrix.at[row_id, col])
            for col in fitted_predictors(cph)
        )
    )


def test_numeric_contribution_is_beta_times_deviation_from_mean(fitted: tuple[Any, ...]) -> None:
    cph, matrix, _, _, _, contributions = fitted
    for position, row_id in enumerate(matrix.index[:25]):
        numeric = [c for c in contributions[position] if c.kind == "numeric"]
        assert {c.feature for c in numeric} == {"contract_length_months", "usage_frequency"}
        for record in numeric:
            beta = float(cph.params_[record.column])
            mean = float(matrix[record.column].mean())
            value = float(matrix.at[row_id, record.column])
            assert record.reference == pytest.approx(mean, rel=1e-15)
            assert record.value == value
            assert record.contribution == pytest.approx(beta * (value - mean), rel=1e-12)


def test_categorical_one_active_level_and_reference_contributes_zero(
    fitted: tuple[Any, ...],
) -> None:
    cph, matrix, specs, _, _, contributions = fitted
    reference = next(spec for spec in specs if spec.name == "plan_tier").categories[0]
    seen_reference = seen_other = False
    for position, row_id in enumerate(matrix.index):
        plan = [c for c in contributions[position] if c.feature == "plan_tier"]
        assert len(plan) <= 1  # one-hot: never more than one active level
        active = [
            col
            for col in fitted_predictors(cph)
            if col.startswith("plan_tier_") and float(matrix.at[row_id, col]) == 1.0
        ]
        if not active:
            seen_reference = True
            assert plan == []  # reference category: no record, contributes 0
        else:
            seen_other = True
            assert plan[0].column == active[0]
            assert plan[0].contribution == float(cph.params_[active[0]])
            assert plan[0].reference == reference
            assert plan[0].value == active[0].removeprefix("plan_tier_")
    assert seen_reference and seen_other


def test_sum_and_inverse_reconstruction(fitted: tuple[Any, ...]) -> None:
    cph, matrix, _, baseline, relative, contributions = fitted
    for position, row_id in enumerate(matrix.index):
        total = sum(c.contribution for c in contributions[position])
        assert relative[position] == pytest.approx(total, abs=1e-9)
        assert _linear_predictor(cph, matrix, row_id) == pytest.approx(
            baseline + relative[position], abs=1e-9
        )


def test_reconstruction_for_reference_and_extreme_profiles(fitted: tuple[Any, ...]) -> None:
    cph, matrix, specs, baseline, _, _ = fitted
    probe = matrix.iloc[:3].copy()
    for col in fitted_predictors(cph):
        if col.startswith("plan_tier_"):
            probe[col] = 0.0  # all-reference-category customers
    probe.loc[probe.index[1], "usage_frequency"] = 1e4
    probe.loc[probe.index[2], "contract_length_months"] = -500.0
    base, relative, contributions = feature_contributions(cph, probe, matrix, specs)
    assert base == baseline
    for position, row_id in enumerate(probe.index):
        assert all(c.feature != "plan_tier" for c in contributions[position])
        assert _linear_predictor(cph, probe, row_id) == pytest.approx(
            base + relative[position], abs=1e-9
        )


def test_reliability_is_provenance_not_a_filter(fitted: tuple[Any, ...]) -> None:
    cph, _, _, _, _, contributions = fitted
    reliable = {
        item["feature"]: not (item["ci_lower"] <= 1.0 <= item["ci_upper"])
        for item in feature_associations(cph)
    }
    flat = [c for row in contributions for c in row]
    assert {c.column for c in flat} == set(fitted_predictors(cph))
    for record in flat:
        assert record.reliable is reliable[record.column]


def test_run_output_aligned_to_scored_subset_never_imputed() -> None:
    records = synthetic_dataset(missing_fraction=0.1)
    output = run_node2(records, load_node2_config("1"), PREDICTORS, now=NOW)
    assert output.model_type == ModelType.COX_PH
    assert output.baseline_log_hazard is not None
    assert output.customer_contributions is not None
    assert output.customer_relative_log_hazard is not None
    n_scored = sum(1 for s in output.customer_states if s == CustomerState.SCORED)
    assert n_scored < len(output.customer_ids)  # excluded customers get no slot
    assert len(output.customer_contributions) == n_scored
    assert len(output.customer_relative_log_hazard) == n_scored
    assert output.risk_scores is not None and len(output.risk_scores) == n_scored


def test_relative_log_hazard_is_monotone_with_risk_score() -> None:
    output = run_node2(synthetic_dataset(), load_node2_config("1"), PREDICTORS, now=NOW)
    assert output.risk_scores is not None
    assert output.customer_relative_log_hazard is not None
    relative = [float(value or 0.0) for value in output.customer_relative_log_hazard]
    pairs = sorted(zip(relative, output.risk_scores, strict=True))
    scores = [score for _, score in pairs]
    assert all(b >= a - 1e-12 for a, b in zip(scores, scores[1:], strict=False))


def test_km_path_has_no_contributions() -> None:
    output = run_node2(synthetic_dataset(), load_node2_config("1"), [], now=NOW)
    assert output.model_type == ModelType.KAPLAN_MEIER
    assert output.customer_contributions is None
    assert output.customer_relative_log_hazard is None
    assert output.baseline_log_hazard is None


def test_stratified_fit_has_no_record_for_the_strata_feature() -> None:
    config = load_node2_config("1")
    _, _, specs, matrix = _prepare(synthetic_dataset(), PREDICTORS, config)
    cph = fit_cox(matrix, config, strata="plan_tier__raw")
    baseline, relative, contributions = feature_contributions(cph, matrix, matrix, specs)
    assert all(c.feature != "plan_tier" for row in contributions for c in row)
    for position, row_id in enumerate(matrix.index):
        assert _linear_predictor(cph, matrix, row_id) == pytest.approx(
            baseline + relative[position], abs=1e-9
        )


def test_value_at_training_mean_contributes_zero(fitted: tuple[Any, ...]) -> None:
    # A constant column is rejected by eligibility (no variation) before a fit;
    # the equivalent property is that a value at the mean contributes exactly 0.
    cph, matrix, specs, _, _, _ = fitted
    probe = matrix.iloc[:2].copy()
    probe["usage_frequency"] = float(matrix["usage_frequency"].astype(float).mean())
    _, _, contributions = feature_contributions(cph, probe, matrix, specs)
    usage = [c for row in contributions for c in row if c.feature == "usage_frequency"]
    assert len(usage) == 2 and all(c.contribution == 0.0 for c in usage)


def test_contributions_are_deterministic_and_full_precision() -> None:
    records = synthetic_dataset()
    artifact = fit_model(records, load_node2_config("1"), PREDICTORS, now=NOW)
    first = score_to_output(artifact, records).model_dump_json()
    second = score_to_output(artifact, records).model_dump_json()
    assert first == second
    output = Node2Output.model_validate_json(first)
    assert output.customer_contributions is not None
    numeric = [c for row in output.customer_contributions for c in row if c.kind == "numeric"]
    # Never rounded: values keep more than 6 decimal places.
    assert any(c.contribution != round(c.contribution, 6) for c in numeric)


def test_old_output_without_contribution_fields_validates() -> None:
    output = Node2Output.model_validate(
        {
            "model_type": "cox_ph",
            "model_status": "READY",
            "model_version": "abc",
            "survival_probabilities": {},
        }
    )
    assert output.customer_contributions is None
    assert output.customer_relative_log_hazard is None
    assert output.baseline_log_hazard is None

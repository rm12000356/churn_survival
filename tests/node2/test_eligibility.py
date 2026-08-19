"""Model eligibility hard gates + warnings (ROADMAP Task 3.1/§2.4)."""

from __future__ import annotations

import pandas as pd

from node2.eligibility import check_eligibility
from node2.node import _prepare
from tests.node2.conftest import PREDICTORS, make_config, make_record, synthetic_dataset


def _matrix(records, predictors=PREDICTORS, config=None):
    """Complete-case model matrix over the scored subset (mirrors the pipeline)."""
    _, _, specs, matrix = _prepare(records, predictors, config or make_config())
    return matrix, specs


def test_eligible_dataset_passes_all_gates() -> None:
    records = synthetic_dataset()
    matrix, specs = _matrix(records)
    result = check_eligibility(matrix, specs, make_config(), n_input_records=len(records))
    assert result.eligible
    assert result.hard_failures == ()


def test_min_customers_gate() -> None:
    records = synthetic_dataset(n=150)
    matrix, specs = _matrix(records)
    result = check_eligibility(matrix, specs, make_config(), n_input_records=len(records))
    assert not result.eligible
    assert any("min customers gate" in failure for failure in result.hard_failures)


def test_min_events_gate() -> None:
    records = synthetic_dataset(event_rate=0.02)
    matrix, specs = _matrix(records)
    result = check_eligibility(matrix, specs, make_config(), n_input_records=len(records))
    assert not result.eligible
    assert any("min events gate" in failure for failure in result.hard_failures)


def test_events_per_predictor_gate() -> None:
    config = make_config(min_events_per_predictor=10**6)
    records = synthetic_dataset()
    matrix, specs = _matrix(records)
    result = check_eligibility(matrix, specs, config, n_input_records=len(records))
    assert not result.eligible
    assert any("events-per-predictor gate" in failure for failure in result.hard_failures)


def test_constant_numeric_predictor_fails_variation() -> None:
    records = [make_record(i, tenure=100 + i, event=i % 2) for i in range(210)]
    matrix, specs = _matrix(records, predictors=["contract_length_months"])
    result = check_eligibility(matrix, specs, make_config(), n_input_records=len(records))
    assert not result.eligible
    assert any("no meaningful variation" in failure for failure in result.hard_failures)


def test_single_category_categorical_fails_variation() -> None:
    records = [make_record(i, tenure=100 + i, event=i % 2, plan_tier="basic") for i in range(210)]
    matrix, specs = _matrix(records, predictors=["plan_tier"])
    result = check_eligibility(matrix, specs, make_config(), n_input_records=len(records))
    assert not result.eligible
    assert any("only its reference category" in failure for failure in result.hard_failures)


def test_missingness_gate_excludes_more_than_threshold() -> None:
    config = make_config(missingness_threshold=0.1)
    records = synthetic_dataset(missing_fraction=0.5)
    matrix, specs = _matrix(records)
    result = check_eligibility(matrix, specs, config, n_input_records=len(records))
    assert not result.eligible
    assert any("missingness gate" in failure for failure in result.hard_failures)


def test_non_binary_event_is_hard_failure() -> None:
    matrix = pd.DataFrame(
        {"duration": [10.0, 20.0], "event": [0, 2], "plan_tier_pro": [0.0, 1.0]},
        index=["a", "b"],
    )
    result = check_eligibility(matrix, [], make_config(), n_input_records=2)
    assert not result.eligible
    assert any("strictly binary" in failure for failure in result.hard_failures)


def test_low_event_count_warns() -> None:
    records = synthetic_dataset(n=400, event_rate=0.12)
    matrix, specs = _matrix(records)
    result = check_eligibility(matrix, specs, make_config(), n_input_records=len(records))
    assert result.eligible
    assert any("low absolute event count" in warning for warning in result.warnings)


def test_elevated_missingness_warns() -> None:
    records = synthetic_dataset(missing_fraction=0.1)
    matrix, specs = _matrix(records)
    result = check_eligibility(matrix, specs, make_config(), n_input_records=len(records))
    assert any("elevated missingness" in warning for warning in result.warnings)


def test_short_followup_warns() -> None:
    records = synthetic_dataset(tenure_scale=20.0)
    matrix, specs = _matrix(records)
    result = check_eligibility(matrix, specs, make_config(), n_input_records=len(records))
    assert any("short overall follow-up" in warning for warning in result.warnings)

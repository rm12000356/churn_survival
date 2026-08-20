"""Node 2 end-to-end: fit/score separation, determinism, alignment (ROADMAP Task 3.2/3.12/§2.13)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from config.loader import load_node2_config
from node2.artifact import load_artifact, save_artifact
from node2.node import (
    _reference_date,
    fit_model,
    main,
    run_node2,
    score_customers,
    score_to_output,
)
from schemas.enums import CustomerState, HorizonStatus, ModelStatus, ModelType
from tests.node2.conftest import PREDICTORS, make_config, make_record, synthetic_dataset

NOW = datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)


def test_full_run_cox_path() -> None:
    records = synthetic_dataset()
    output = run_node2(records, load_node2_config("1"), PREDICTORS, now=NOW)
    assert output.model_type == ModelType.COX_PH
    assert output.model_status in (ModelStatus.READY, ModelStatus.WARNING)
    assert output.risk_scores is not None
    assert len(output.risk_scores) == len(records)
    assert all(0.0 <= score <= 1.0 for score in output.risk_scores)
    assert output.survival_probabilities["90d"].status == HorizonStatus.AVAILABLE
    assert output.feature_associations is not None


def test_interpretation_phrasing_by_feature_kind() -> None:
    output = run_node2(synthetic_dataset(), load_node2_config("1"), PREDICTORS, now=NOW)
    assert output.feature_associations is not None
    by_feature = {a.feature: a.interpretation for a in output.feature_associations}
    assert "one-unit increase" in by_feature["contract_length_months"]
    assert "reference category" not in by_feature["contract_length_months"]
    assert "reference category" in by_feature["plan_tier_pro"]


def test_run_is_deterministic() -> None:
    records = synthetic_dataset()
    config = load_node2_config("1")
    a = run_node2(records, config, PREDICTORS, now=NOW)
    b = run_node2(records, config, PREDICTORS, now=NOW)
    assert a.risk_scores == b.risk_scores
    assert a.model_version == b.model_version
    for key in a.survival_probabilities:
        assert a.survival_probabilities[key].values == b.survival_probabilities[key].values


def test_customer_ids_sorted_and_states_parallel() -> None:
    records = synthetic_dataset()
    output = run_node2(records, load_node2_config("1"), PREDICTORS, now=NOW)
    assert output.customer_ids == sorted(output.customer_ids)
    assert len(output.customer_ids) == len(output.customer_states) == len(records)
    scored_indices = [
        i for i, state in enumerate(output.customer_states) if state == CustomerState.SCORED
    ]
    assert len(scored_indices) == len(output.risk_scores)
    for horizon in output.survival_probabilities.values():
        if horizon.status == HorizonStatus.AVAILABLE:
            assert len(horizon.values) == len(scored_indices)
            assert len(horizon.ci) == len(scored_indices)


def test_empty_dataset_is_insufficient_data() -> None:
    output = run_node2([], load_node2_config("1"), PREDICTORS, now=NOW)
    assert output.model_type == ModelType.NONE
    assert output.model_status == ModelStatus.INSUFFICIENT_DATA
    assert output.risk_scores is None
    assert output.customer_ids == []
    assert all(
        h.status == HorizonStatus.INSUFFICIENT_DATA for h in output.survival_probabilities.values()
    )


def test_fit_then_score_matches_single_run() -> None:
    records = synthetic_dataset()
    config = load_node2_config("1")
    artifact = fit_model(records, config, PREDICTORS, now=NOW)
    scored = score_customers(artifact, records)
    output = score_to_output(artifact, records)
    assert scored["risk_scores"] == output.risk_scores
    assert scored["model_type"] == ModelType.COX_PH


def test_rescore_after_persist_is_identical(tmp_path: Path) -> None:
    records = synthetic_dataset()
    config = load_node2_config("1")
    artifact = fit_model(records, config, PREDICTORS, now=NOW)
    before = score_customers(artifact, records)["risk_scores"]
    artifact_dir = save_artifact(artifact, tmp_path)
    loaded = load_artifact(artifact_dir)
    after = score_customers(loaded, records)["risk_scores"]
    assert after == before


def test_fallback_to_km_when_ineligible() -> None:
    records = synthetic_dataset()
    config = make_config(min_events=10**9)
    output = run_node2(records, config, PREDICTORS, now=NOW)
    assert output.model_type == ModelType.KAPLAN_MEIER
    assert output.model_status == ModelStatus.FALLBACK
    assert output.risk_scores is None
    assert output.feature_associations is None
    assert any("fell back to Kaplan-Meier" in w for w in output.warnings)


def test_empty_predictors_produces_global_km() -> None:
    records = synthetic_dataset()
    output = run_node2(records, load_node2_config("1"), [], now=NOW)
    assert output.model_type == ModelType.KAPLAN_MEIER
    assert output.model_status == ModelStatus.FALLBACK
    assert output.risk_scores is None
    assert output.feature_associations is None
    scored = sum(1 for s in output.customer_states if s == CustomerState.SCORED)
    assert scored > 0
    assert any("fell back to Kaplan-Meier" in w for w in output.warnings)
    available = [
        h for h in output.survival_probabilities.values() if h.status == HorizonStatus.AVAILABLE
    ]
    assert available
    for horizon in available:
        assert len(horizon.values) == scored
        assert len(set(round(v, 6) for v in horizon.values)) == 1


def test_excluded_customers_carry_no_scores() -> None:
    records = synthetic_dataset(missing_fraction=0.05)
    output = run_node2(records, load_node2_config("1"), PREDICTORS, now=NOW)
    scored = sum(1 for s in output.customer_states if s == CustomerState.SCORED)
    assert output.risk_scores is not None
    assert scored == len(output.risk_scores)


def test_cold_start_customers_receive_no_risk_scores() -> None:
    cold_start_ids = [f"cus_{i:04d}" for i in range(9000, 9010)]
    records = synthetic_dataset() + [
        make_record(
            i,
            tenure=10,
            event=0,
            plan_tier=None,
            contract_length_months=None,
            usage_frequency=None,
        )
        for i in range(9000, 9010)
    ]
    output = run_node2(records, load_node2_config("1"), PREDICTORS, now=NOW)
    assert output.risk_scores is not None
    states = dict(zip(output.customer_ids, output.customer_states, strict=False))
    for customer_id in cold_start_ids:
        assert states[customer_id] == CustomerState.NOT_ENOUGH_DATA
    scored = sum(1 for s in output.customer_states if s == CustomerState.SCORED)
    assert len(output.risk_scores) == scored


def test_model_version_is_fingerprint() -> None:
    records = synthetic_dataset()
    config = load_node2_config("1")
    artifact = fit_model(records, config, PREDICTORS, now=NOW)
    assert len(artifact.metadata.model_version) == 16
    assert artifact.metadata.training_dataset_version
    assert artifact.metadata.n_customers == len(records)


def test_model_version_content_addressed() -> None:
    config = load_node2_config("1")
    a = run_node2(synthetic_dataset(seed=1), config, PREDICTORS, now=NOW)
    b = run_node2(synthetic_dataset(seed=2), config, PREDICTORS, now=NOW)
    c = run_node2(synthetic_dataset(seed=1), config, PREDICTORS, now=NOW)
    assert a.model_version != b.model_version
    assert a.model_version == c.model_version


def test_pure_provenance_change_keeps_version_and_ci() -> None:
    config = load_node2_config("1")
    a = run_node2(synthetic_dataset(seed=1), config, PREDICTORS, now=NOW)
    records = [
        record.model_copy(
            update={
                "meta": record.meta.model_copy(update={"mapping_version": "map_other_v9"})
            }
        )
        for record in synthetic_dataset(seed=1)
    ]
    b = run_node2(records, config, PREDICTORS, now=NOW)
    assert a.model_version == b.model_version
    assert a.validation_metrics == b.validation_metrics


def test_fit_model_empty_dataset_raises() -> None:
    with pytest.raises(ValueError):
        fit_model([], load_node2_config("1"), PREDICTORS)
    with pytest.raises(ValueError):
        _reference_date([])


def test_fit_model_accepts_explicit_dataset_version() -> None:
    records = synthetic_dataset()
    artifact = fit_model(
        records,
        load_node2_config("1"),
        PREDICTORS,
        dataset_version="explicit_v1",
        now=NOW,
    )
    assert artifact.metadata.training_dataset_version == "explicit_v1"


def test_fit_model_rejects_mixed_reference_dates() -> None:
    records = synthetic_dataset(n=10)
    first = records[0].model_copy(
        update={
            "meta": records[0].meta.model_copy(update={"reference_date": date(2025, 1, 1)})
        }
    )
    with pytest.raises(ValueError):
        _reference_date([first, *records[1:]])


def test_all_excluded_dataset_yields_insufficient_data(tmp_path: Path) -> None:
    records = [make_record(i, tenure=0, event=0) for i in range(5)]
    artifact = fit_model(records, load_node2_config("1"), PREDICTORS, now=NOW)
    assert artifact.model_type == ModelType.NONE
    assert artifact.model_status == ModelStatus.INSUFFICIENT_DATA
    assert artifact.model is None and artifact.km is None
    output = score_to_output(artifact, records)
    assert output.risk_scores is None


def test_technical_fit_failure_falls_back(monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("solver diverged")

    monkeypatch.setattr("node2.node.fit_cox", _boom)
    records = synthetic_dataset()
    artifact = fit_model(records, load_node2_config("1"), PREDICTORS, now=NOW)
    assert artifact.model is None
    assert artifact.model_type == ModelType.KAPLAN_MEIER
    assert artifact.model_status == ModelStatus.FALLBACK
    assert any("CoxPH fit failed (technical)" in w for w in artifact.warnings)


def test_assumption_fallback_warns(monkeypatch) -> None:
    from node2.assumptions import AssumptionResult

    def _fake_assumptions(cph, matrix, specs, config, *, seed):
        return AssumptionResult(
            ph_p_values={}, severity="serious", decision="fallback",
            c_index=None, c_index_ci=None,
        )

    monkeypatch.setattr("node2.node.run_assumptions", _fake_assumptions)
    records = synthetic_dataset()
    artifact = fit_model(records, load_node2_config("1"), PREDICTORS, now=NOW)
    assert artifact.model is None
    assert artifact.model_status == ModelStatus.FALLBACK
    assert any("serious proportional-hazards violation" in w for w in artifact.warnings)


def test_assumption_stratify_through_fit_model(monkeypatch) -> None:
    from node2.assumptions import AssumptionResult

    def _fake_assumptions(cph, matrix, specs, config, *, seed):
        return AssumptionResult(
            ph_p_values={}, severity="serious", decision="stratify",
            c_index=0.6, c_index_ci=(0.5, 0.7),
            strata_used="plan_tier__raw", refitted_model=cph,
        )

    monkeypatch.setattr("node2.node.run_assumptions", _fake_assumptions)
    records = synthetic_dataset()
    artifact = fit_model(records, load_node2_config("1"), PREDICTORS, now=NOW)
    assert artifact.model is not None
    assert artifact.metadata.baseline["strata_used"] == "plan_tier__raw"
    assert artifact.model_status == ModelStatus.WARNING


def test_stratification_sole_categorical_does_not_emit_scores() -> None:
    records = synthetic_dataset()
    config = make_config(ph_p_value_serious=1.0)
    output = run_node2(records, config, ["plan_tier"], now=NOW)
    assert output.risk_scores is None
    assert output.model_status in (ModelStatus.FALLBACK, ModelStatus.INSUFFICIENT_DATA)
    assert output.model_type == ModelType.KAPLAN_MEIER
    assert any("fell back to Kaplan-Meier" in w for w in output.warnings)


def test_cli_usage_errors() -> None:
    assert main([]) == 2
    assert main(["file.csv", "--config"]) == 2
    assert main(["file.csv", "--model-config"]) == 2


def test_cli_full_run(tmp_path: Path, fresh_settings, monkeypatch) -> None:
    monkeypatch.setenv("MODEL_DIR", str(tmp_path))
    fixture = Path(__file__).parent.parent / "adapters" / "fixtures" / "clean_customers.csv"
    code = main([str(fixture)])
    assert code == 0
    assert any(p.is_dir() for p in tmp_path.iterdir())


def test_cli_full_run_with_flags(tmp_path: Path, fresh_settings, monkeypatch) -> None:
    monkeypatch.setenv("MODEL_DIR", str(tmp_path))
    fixture = Path(__file__).parent.parent / "adapters" / "fixtures" / "clean_customers.csv"
    code = main(["--config", "1", "--model-config", "1", str(fixture)])
    assert code == 0
    assert any(p.is_dir() for p in tmp_path.iterdir())


def test_cli_empty_dataset_returns_zero(monkeypatch, fresh_settings) -> None:
    class _EmptyOutput:
        canonical_dataset = []

    monkeypatch.setattr("node1.node.run_node1", lambda *a, **k: _EmptyOutput())
    assert main(["whatever.csv"]) == 0


def test_cli_node1_failure_returns_error(monkeypatch) -> None:
    def _boom(*args, **kwargs):
        raise RuntimeError("node1 exploded")

    monkeypatch.setattr("node1.node.run_node1", _boom)
    assert main(["whatever.csv"]) == 1

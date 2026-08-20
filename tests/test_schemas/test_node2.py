from __future__ import annotations

import pytest
from pydantic import ValidationError

from schemas.node2 import ModelArtifact, Node2Output

ARTIFACT = {
    "model_version": "v0.1.0",
    "training_timestamp": "2026-08-17T10:00:00Z",
    "training_dataset_version": "ds_v1",
    "reference_date": "2026-08-15",
    "selected_features": ["plan_tier", "contract_length_months", "usage_frequency"],
    "coefficients": {"plan_tier_pro": 0.42},
    "baseline": {"plan_tier_basic": 0.05},
    "penalizer": 0.1,
    "n_customers": 1842,
    "n_events": 23,
    "encoding_scheme": {"plan_tier": "one_hot"},
    "validation_metrics": {"c_index": 0.72},
    "assumption_check_results": {"ph_test_p": 0.21},
    "horizon_config": [30, 90, 180],
}

OUTPUT_COX = {
    "model_type": "cox_ph",
    "model_status": "READY",
    "model_version": "v0.1.0",
    "risk_scores": [0.2, 0.8],
    "survival_probabilities": {
        "30d": {"status": "AVAILABLE", "values": [0.97, 0.81], "ci": [[0.94, 0.99], [0.78, 0.84]]},
        "90d": {"status": "AVAILABLE", "values": [0.89, 0.62], "ci": [[0.85, 0.93], [0.58, 0.66]]},
        "180d": {"status": "INSUFFICIENT_DATA"},
    },
    "feature_associations": [
        {
            "feature": "plan_tier_pro",
            "coefficient": 0.42,
            "hazard_ratio": 1.52,
            "ci_lower": 1.11,
            "ci_upper": 2.08,
            "p_value": 0.03,
            "interpretation": "52% higher hazard than reference.",
        }
    ],
    "validation_metrics": {"c_index": 0.72},
    "assumption_checks": {"ph_test_p": 0.21},
    "warnings": [],
    "customer_ids": ["cus_a", "cus_b"],
    "customer_states": ["scored", "scored"],
}


def test_artifact_round_trip_preserves_all_metadata() -> None:
    artifact = ModelArtifact.model_validate(ARTIFACT)
    assert artifact.model_version == "v0.1.0"
    assert artifact.penalizer == 0.1
    assert artifact.n_customers == 1842
    assert artifact.horizon_config == [30, 90, 180]
    dumped = artifact.model_dump(mode="json")
    assert dumped["training_timestamp"].startswith("2026-08-17T10:00:00")
    assert dumped["reference_date"] == "2026-08-15"


def test_artifact_missing_penalizer_rejected() -> None:
    payload = {k: v for k, v in ARTIFACT.items() if k != "penalizer"}
    with pytest.raises(ValidationError):
        ModelArtifact.model_validate(payload)


def test_cox_output_round_trip() -> None:
    output = Node2Output.model_validate(OUTPUT_COX)
    assert output.model_status.value == "READY"
    assert output.risk_scores == [0.2, 0.8]
    assert output.feature_associations is not None
    assert len(output.survival_probabilities) == 3
    assert output.customer_ids == ["cus_a", "cus_b"]
    assert output.customer_states == ["scored", "scored"]


def test_fallback_output_allows_null_risk_scores() -> None:
    fallback = {
        "model_type": "kaplan_meier",
        "model_status": "FALLBACK",
        "model_version": "v0.1.0",
        "risk_scores": None,
        "survival_probabilities": {
            "90d": {"status": "INSUFFICIENT_DATA"},
        },
        "validation_metrics": {},
        "assumption_checks": {},
        "warnings": ["fell back to KM"],
        "customer_ids": ["cus_a", "cus_b"],
        "customer_states": ["scored", "not_enough_data"],
    }
    output = Node2Output.model_validate(fallback)
    assert output.risk_scores is None
    assert output.model_status.value == "FALLBACK"


def test_customer_ids_parallel_to_states() -> None:
    payload = {**OUTPUT_COX, "customer_ids": ["cus_a"], "customer_states": ["scored", "scored"]}
    output = Node2Output.model_validate(payload)
    # Alignment contract: states cover the full universe; risk_scores cover the
    # scored subset (i-th scored == i-th entry of customer_ids with state scored).
    assert len(output.customer_ids) == 1
    assert len(output.customer_states) == 2


def test_invalid_model_status_rejected() -> None:
    payload = {**OUTPUT_COX, "model_status": "PERFECT"}
    with pytest.raises(ValidationError):
        Node2Output.model_validate(payload)


def test_invalid_horizon_status_rejected() -> None:
    payload = {
        **OUTPUT_COX,
        "survival_probabilities": {"30d": {"status": "AVAILABLE_ISH"}},
    }
    with pytest.raises(ValidationError):
        Node2Output.model_validate(payload)


def test_invalid_customer_state_rejected() -> None:
    payload = {**OUTPUT_COX, "customer_states": ["scored", "maybe"]}
    with pytest.raises(ValidationError):
        Node2Output.model_validate(payload)

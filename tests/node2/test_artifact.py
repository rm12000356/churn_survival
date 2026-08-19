"""Model artifact + versioning (ROADMAP Task 3.10/§2.11)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from config.loader import load_node2_config
from node2.artifact import (
    build_metadata,
    derive_model_version,
    load_artifact,
    save_artifact,
)
from node2.node import fit_model
from tests.node2.conftest import PREDICTORS, synthetic_dataset


def test_derive_model_version_deterministic_and_sensitive() -> None:
    kwargs = dict(
        reference_date=date(2026, 8, 15),
        dataset_version="ds_v1",
        config=load_node2_config("1"),
        selected_features=["plan_tier", "usage_frequency"],
    )
    a = derive_model_version(**kwargs)
    b = derive_model_version(**kwargs)
    assert a == b
    changed = dict(kwargs)
    changed["config"] = load_node2_config("1").model_copy(update={"penalizer": 0.5})
    c = derive_model_version(**changed)
    assert c != a
    fewer_features = dict(kwargs)
    fewer_features["selected_features"] = ["plan_tier"]
    d = derive_model_version(**fewer_features)
    assert d != a


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    artifact = fit_model(synthetic_dataset(), load_node2_config("1"), PREDICTORS)
    artifact_dir = save_artifact(artifact, tmp_path)
    assert (artifact_dir / "model.json").is_file()
    assert (artifact_dir / "model.joblib").is_file()

    loaded = load_artifact(artifact_dir)
    assert loaded.metadata.model_version == artifact.metadata.model_version
    assert loaded.model is not None
    assert loaded.specs == artifact.specs
    assert loaded.model_status == artifact.model_status


def test_load_artifact_missing_sidecar_raises(tmp_path: Path) -> None:
    artifact = fit_model(synthetic_dataset(), load_node2_config("1"), PREDICTORS)
    artifact_dir = save_artifact(artifact, tmp_path)
    (artifact_dir / "model.joblib").unlink()
    with pytest.raises(FileNotFoundError):
        load_artifact(artifact_dir)


def test_load_artifact_version_mismatch_raises(tmp_path: Path) -> None:
    artifact = fit_model(synthetic_dataset(), load_node2_config("1"), PREDICTORS)
    artifact_dir = save_artifact(artifact, tmp_path)
    sidecar = json.loads((artifact_dir / "model.json").read_text(encoding="utf-8"))
    sidecar["model_version"] = "corrupted"
    (artifact_dir / "model.json").write_text(json.dumps(sidecar), encoding="utf-8")
    with pytest.raises(ValueError):
        load_artifact(artifact_dir)


def test_build_metadata_contract() -> None:
    metadata = build_metadata(
        model_version="abc123",
        dataset_version="ds_v1",
        reference_date=date(2026, 8, 15),
        selected_features=["plan_tier"],
        coefficients={"plan_tier_pro": 0.42},
        baseline={"risk_reference_time_days": 90.0},
        penalizer=0.1,
        n_customers=400,
        n_events=140,
        encoding_scheme={"plan_tier": {"kind": "categorical"}},
        validation_metrics={"c_index": 0.72},
        assumption_check_results={"severity": "none"},
        horizon_config=[30, 90, 180],
        training_timestamp=datetime(2026, 8, 17, 12, 0, 0),
    )
    assert metadata.model_version == "abc123"
    assert metadata.n_customers == 400
    assert metadata.horizon_config == [30, 90, 180]

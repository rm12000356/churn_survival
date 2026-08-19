"""Model artifact & versioning (architecture §2.11, ROADMAP Task 3.10).

A fitted artifact is persisted as two files under ``models/<model_version>/``:

- ``model.json`` — the JSON sidecar: every versioning/metadata field from
  §2.11, validated by the ``ModelArtifact`` schema.
- ``model.joblib`` — the pickled fitted objects (CoxPHFitter or KM curves,
  encoding specs, fit data). Enables *exact* reproduction of any historical
  score (fit/score separation, §2.2).

``model_version`` is derived deterministically from the inputs, so identical
runs overwrite the same artifact path (reproducibility, §6).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
from lifelines import CoxPHFitter

from config.models import Node2Config
from node2.kaplan_meier import KMResult
from node2.matrix import FeatureSpec
from schemas.enums import HorizonStatus, ModelStatus, ModelType
from schemas.node2 import FeatureAssociation, ModelArtifact


@dataclass(frozen=True)
class FittedArtifact:
    """A fully versioned, rescoreable model artifact (§2.2/§2.11)."""

    metadata: ModelArtifact
    model: CoxPHFitter | None
    km: KMResult | None
    specs: tuple[FeatureSpec, ...]
    fit_data: pd.DataFrame
    t_ref: float
    horizon_statuses: dict[int, HorizonStatus]
    model_type: ModelType
    model_status: ModelStatus
    config: Node2Config
    warnings: tuple[str, ...] = ()
    feature_associations: tuple[FeatureAssociation, ...] = ()


def derive_model_version(
    *,
    reference_date: Any,
    dataset_version: str,
    config: Node2Config,
    selected_features: list[str],
) -> str:
    """Deterministic model version: same inputs -> same version string."""
    payload = "|".join(
        [
            str(reference_date),
            dataset_version,
            config.eligibility_version,
            config.modeling_version,
            config.horizon_version,
            f"{config.penalizer:.6f}",
            config.tie_method,
            ",".join(str(t) for t in config.horizons),
            ",".join(sorted(selected_features)),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def save_artifact(artifact: FittedArtifact, model_dir: Path) -> Path:
    """Persist ``model.json`` + ``model.joblib``; returns the artifact directory."""
    artifact_dir = model_dir / artifact.metadata.model_version
    artifact_dir.mkdir(parents=True, exist_ok=True)
    sidecar = artifact.metadata.model_dump(mode="json")
    (artifact_dir / "model.json").write_text(
        json.dumps(sidecar, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    joblib.dump(artifact, artifact_dir / "model.joblib")
    return artifact_dir


def load_artifact(artifact_dir: Path) -> FittedArtifact:
    """Load a persisted artifact (requires both sidecar files)."""
    artifact_dir = Path(artifact_dir)
    joblib_path = artifact_dir / "model.joblib"
    sidecar_path = artifact_dir / "model.json"
    if not joblib_path.is_file() or not sidecar_path.is_file():
        raise FileNotFoundError(
            f"incomplete model artifact in {artifact_dir}: missing model.joblib or model.json"
        )
    artifact: FittedArtifact = joblib.load(joblib_path)
    metadata = ModelArtifact.model_validate(json.loads(sidecar_path.read_text(encoding="utf-8")))
    if metadata.model_version != artifact.metadata.model_version:
        raise ValueError("sidecar model_version does not match joblib artifact")
    return artifact


def build_metadata(
    *,
    model_version: str,
    dataset_version: str,
    reference_date: Any,
    selected_features: list[str],
    coefficients: dict[str, float],
    baseline: dict[str, Any],
    penalizer: float,
    n_customers: int,
    n_events: int,
    encoding_scheme: dict[str, Any],
    validation_metrics: dict[str, Any],
    assumption_check_results: dict[str, Any],
    horizon_config: list[int],
    training_timestamp: datetime | None = None,
) -> ModelArtifact:
    """Assemble the §2.11 sidecar from fit results."""
    return ModelArtifact(
        model_version=model_version,
        training_timestamp=training_timestamp or datetime.now(UTC),
        training_dataset_version=dataset_version,
        reference_date=reference_date,
        selected_features=selected_features,
        coefficients=coefficients,
        baseline=baseline,
        penalizer=penalizer,
        n_customers=n_customers,
        n_events=n_events,
        encoding_scheme=encoding_scheme,
        validation_metrics=validation_metrics,
        assumption_check_results=assumption_check_results,
        horizon_config=horizon_config,
    )

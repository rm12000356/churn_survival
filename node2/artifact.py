from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
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


FIT_ALGORITHM_VERSION = "fit-2"


def derive_model_version(
    *,
    reference_date: Any,
    dataset_version: str,
    config: Node2Config,
    selected_features: list[str],
) -> str:
    payload = "|".join(
        [
            str(reference_date),
            dataset_version,
            FIT_ALGORITHM_VERSION,
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


_SAFE_VERSION = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
DIGEST_FILE = "model.sha256"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_ARTIFACT_FILES = ("model.joblib", "model.json", DIGEST_FILE)
_SAVE_LOCK = threading.Lock()


def _is_complete(artifact_dir: Path) -> bool:
    joblib_path = artifact_dir / "model.joblib"
    digest_path = artifact_dir / DIGEST_FILE
    sidecar_path = artifact_dir / "model.json"
    if not (joblib_path.is_file() and digest_path.is_file() and sidecar_path.is_file()):
        return False
    try:
        expected = digest_path.read_text(encoding="utf-8").strip()
        return secrets.compare_digest(expected, _file_sha256(joblib_path))
    except OSError:
        return False


def save_artifact(artifact: FittedArtifact, model_dir: Path) -> Path:
    version = artifact.metadata.model_version
    if not _SAFE_VERSION.match(version):
        raise ValueError(f"invalid model_version {version!r}")
    model_dir = Path(model_dir)
    model_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir = model_dir / version
    with _SAVE_LOCK:
        if _is_complete(artifact_dir):
            os.utime(artifact_dir / "model.json")
            return artifact_dir
        staging = Path(tempfile.mkdtemp(prefix=f".staging-{version}-", dir=model_dir))
        try:
            sidecar = artifact.metadata.model_dump(mode="json")
            (staging / "model.json").write_text(
                json.dumps(sidecar, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            joblib.dump(artifact, staging / "model.joblib")
            (staging / DIGEST_FILE).write_text(
                _file_sha256(staging / "model.joblib") + "\n", encoding="utf-8"
            )
            try:
                os.rename(staging, artifact_dir)
                return artifact_dir
            except OSError:
                pass
            if _is_complete(artifact_dir):
                return artifact_dir
            for name in _ARTIFACT_FILES:
                os.replace(staging / name, artifact_dir / name)
            return artifact_dir
        finally:
            shutil.rmtree(staging, ignore_errors=True)


def load_artifact(artifact_dir: Path) -> FittedArtifact:
    artifact_dir = Path(artifact_dir)
    if not _SAFE_VERSION.match(artifact_dir.name):
        raise ValueError(f"invalid model artifact directory name {artifact_dir.name!r}")
    joblib_path = artifact_dir / "model.joblib"
    sidecar_path = artifact_dir / "model.json"
    digest_path = artifact_dir / DIGEST_FILE
    if not joblib_path.is_file() or not sidecar_path.is_file():
        raise FileNotFoundError(
            f"incomplete model artifact in {artifact_dir}: missing model.joblib or model.json"
        )
    if not digest_path.is_file():
        raise ValueError(
            f"model artifact in {artifact_dir} has no {DIGEST_FILE}; refusing to unpickle "
            "an unverified file (refit to regenerate it)"
        )
    expected = digest_path.read_text(encoding="utf-8").strip()
    if not secrets.compare_digest(expected, _file_sha256(joblib_path)):
        raise ValueError(f"model.joblib in {artifact_dir} does not match its recorded digest")
    artifact: FittedArtifact = joblib.load(joblib_path)
    metadata = ModelArtifact.model_validate(json.loads(sidecar_path.read_text(encoding="utf-8")))
    if metadata.model_version != artifact.metadata.model_version:
        raise ValueError("sidecar model_version does not match joblib artifact")
    return artifact


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


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
    return ModelArtifact(
        model_version=model_version,
        training_timestamp=training_timestamp
        or datetime.combine(_as_date(reference_date), time(0, 0), tzinfo=UTC),
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

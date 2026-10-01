"""Node 1 deployment-config auto-resolution (full-pipeline runs).

A ``run`` on an onboarded dataset must resolve its deployment Node 1 config from
the matched confirmed mapping (``MappingConfig.node1_config_version``) instead of
silently defaulting to ``v1`` — the default's ``approved_core_keys`` may not exist
in the dataset, which used to fail the whole batch with ``COLUMN_MISSINGNESS``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from adapters.mapping_adapter import MappingConfigAdapter
from config.loader import load_config
from config.models import MappingConfig
from orchestration.graph import run_pipeline
from orchestration.mapping import persist_confirmed_mapping
from orchestration.routing import resolve_node1_version
from orchestration.state import PipelineStatus
from router.fingerprint import extract_fingerprint
from router.llm_mapper import confirm_and_persist
from schemas.mapping import MappingReport
from tests.mapping_helpers import mapping_payload


def _report_for(path: Path) -> MappingReport:
    frame = pd.read_csv(path)
    return MappingReport.model_validate(mapping_payload(extract_fingerprint(frame)))


def _adapter(path: Path, node1_config_version: str | None) -> MappingConfigAdapter:
    config = MappingConfig(
        mapping_version="map_test",
        report=_report_for(path),
        node1_config_version=node1_config_version,
    )
    return MappingConfigAdapter(config)


def test_explicit_version_is_untouched(unmapped_csv: Path) -> None:
    assert resolve_node1_version(unmapped_csv, "bank") == ("bank", None)


def test_auto_picks_mapping_deployment_config(unmapped_csv: Path) -> None:
    adapter = _adapter(unmapped_csv, "bank")
    version, warning = resolve_node1_version(unmapped_csv, "auto", adapters=[adapter])
    assert version == "bank"
    assert warning is None


def test_auto_legacy_mapping_warns_and_falls_back(unmapped_csv: Path) -> None:
    adapter = _adapter(unmapped_csv, None)
    version, warning = resolve_node1_version(unmapped_csv, "auto", adapters=[adapter])
    assert version == "1"
    assert warning is not None
    assert "records no deployment Node 1 config" in warning


def test_auto_builtin_adapter_uses_default_without_warning(clean_csv: Path) -> None:
    # clean_csv matches a built-in deterministic adapter; defaulting is normal.
    assert resolve_node1_version(clean_csv, "auto") == ("1", None)


def test_confirm_and_persist_round_trips_node1_config_version(
    unmapped_csv: Path, tmp_path: Path
) -> None:
    config = confirm_and_persist(
        _report_for(unmapped_csv),
        config_dir=tmp_path,
        confirmed_by="reviewer",
        node1_config_version="bank",
    )
    path = tmp_path / "mappings" / f"{config.mapping_version}.json"
    reloaded = load_config(path, MappingConfig)
    assert reloaded.node1_config_version == "bank"


def test_run_pipeline_records_resolved_deployment_config(
    unmapped_csv: Path, tmp_path: Path
) -> None:
    persist_confirmed_mapping(
        _report_for(unmapped_csv),
        config_dir=tmp_path,
        confirmed_by="test",
        node1_config_version="bank",
    )
    result = run_pipeline(unmapped_csv, config_dir=tmp_path)
    assert result.status is PipelineStatus.COMPLETED
    assert result.state.config_versions["node1"] == "bank"
    assert result.state.warnings == [] or not any(
        "auto-resolve" in w for w in result.state.warnings
    )

"""Failure/stop branches of the orchestrator (ROADMAP Task 7.2/7.3)."""

from __future__ import annotations

import importlib
from pathlib import Path

import pandas as pd
import pytest

from config.loader import load_identity_mapping, load_node3_sources_config
from config.settings import get_settings
from orchestration.graph import resume_pipeline, run_pipeline
from orchestration.mapping import CallbackMappingGate
from orchestration.state import PipelineStage, PipelineStatus
from router.fingerprint import extract_fingerprint
from schemas.mapping import MappingReport
from tests.mapping_helpers import mapping_payload


def _report_for(path: Path) -> MappingReport:
    frame = pd.read_csv(path)
    return MappingReport.model_validate(mapping_payload(extract_fingerprint(frame)))


@pytest.mark.parametrize(
    ("module_name", "func_name", "stage"),
    [
        ("node1.node", "run_node1", "node1"),
        ("node2.node", "run_node2", "node2"),
        ("node3.node", "run_node3", "node3"),
        ("node4.node", "run_node4", "node4"),
        ("node5.node", "run_node5", "node5"),
    ],
)
def test_node_exceptions_are_caught_with_partial_state(
    monkeypatch, clean_csv: Path, module_name: str, func_name: str, stage: str
) -> None:
    module = importlib.import_module(module_name)

    def _boom(*args, **kwargs):
        raise RuntimeError(f"synthetic {stage} failure")

    monkeypatch.setattr(module, func_name, _boom)
    result = run_pipeline(clean_csv)
    assert result.status is PipelineStatus.FAILED
    assert any(error["stage"] == stage for error in result.state.errors)
    assert any(error["code"] == "NODE_EXCEPTION" for error in result.state.errors)


def test_mapping_gate_exception_fails(unmapped_csv: Path, tmp_path: Path) -> None:
    def _boom(report, fingerprint):
        raise RuntimeError("gate exploded")

    result = run_pipeline(
        unmapped_csv,
        mapping_gate=CallbackMappingGate(_boom, name="reviewer"),
        mapping_report=_report_for(unmapped_csv),
        config_dir=tmp_path,
    )
    assert result.status is PipelineStatus.FAILED
    assert result.state.stage is PipelineStage.MAPPING_CONFIRMATION
    assert any(error["stage"] == "mapping_confirmation" for error in result.state.errors)


def test_invalid_confirmed_mapping_fails_loudly(unmapped_csv: Path, tmp_path: Path) -> None:
    payload = mapping_payload(extract_fingerprint(pd.read_csv(unmapped_csv)))
    # A target outside the approved core union must be rejected by the confirmation gate.
    payload["proposed_mappings"][4]["target_field"] = "core.not_a_real_key"
    bad_report = MappingReport.model_validate(payload)

    result = run_pipeline(
        unmapped_csv,
        mapping_gate=CallbackMappingGate(lambda report, _fp: report, name="reviewer"),
        mapping_report=bad_report,
        config_dir=tmp_path,
    )
    assert result.status is PipelineStatus.FAILED
    assert any(error["stage"] == "mapping_confirmation" for error in result.state.errors)
    assert not (tmp_path / "mappings").exists()


def test_persist_artifact_writes_the_model(clean_csv: Path, tmp_path: Path) -> None:
    settings = get_settings().model_copy(update={"MODEL_DIR": tmp_path})
    result = run_pipeline(clean_csv, persist_artifact=True, settings=settings)
    assert result.status is PipelineStatus.COMPLETED
    assert result.state.artifact_dir is not None
    assert (Path(result.state.artifact_dir) / "model.json").is_file()
    assert (Path(result.state.artifact_dir) / "model.joblib").is_file()


def test_action_rules_load_failure_is_surfaced(monkeypatch, clean_csv: Path) -> None:
    import orchestration.graph as graph

    def _boom(version):
        raise RuntimeError("no action rules")

    monkeypatch.setattr(graph, "load_action_rules", _boom)
    result = run_pipeline(clean_csv, action_rules=None)
    # The failure is recorded, not silenced; Node 5 then refuses to publish a
    # report whose recommendations lack their required provenance.
    assert result.status is PipelineStatus.FAILED
    assert any("ACTION_RULES unavailable" in warning for warning in result.state.warnings)
    assert any(error["stage"] == "node5" for error in result.state.errors)


def test_resume_pipeline_accepts_a_path(unmapped_csv: Path, tmp_path: Path) -> None:
    stopped = run_pipeline(unmapped_csv, config_dir=tmp_path)
    state_path = stopped.save(tmp_path / "state.json")
    resumed = resume_pipeline(
        str(state_path),
        mapping_gate=CallbackMappingGate(lambda report, _fp: report, name="reviewer"),
        mapping_report=_report_for(unmapped_csv),
        config_dir=tmp_path,
    )
    assert resumed.status is PipelineStatus.COMPLETED


def test_resume_pipeline_rejects_unpersisted_support_inputs(
    clean_csv: Path, tmp_path: Path
) -> None:
    from orchestration.graph import SupportInputsNotResumableError

    result = run_pipeline(
        clean_csv,
        sources_config=load_node3_sources_config("1"),
        identity_mapping=load_identity_mapping("1"),
    )
    assert result.status is PipelineStatus.COMPLETED
    state_path = result.save(tmp_path / "state.json")

    with pytest.raises(SupportInputsNotResumableError):
        resume_pipeline(str(state_path))

    resumed = resume_pipeline(
        str(state_path),
        sources_config=load_node3_sources_config("1"),
        identity_mapping=load_identity_mapping("1"),
    )
    assert resumed.status is PipelineStatus.COMPLETED


def test_external_sources_path(clean_csv: Path) -> None:
    result = run_pipeline(
        clean_csv,
        sources_config=load_node3_sources_config("1"),
        identity_mapping=load_identity_mapping("1"),
    )
    assert result.status is PipelineStatus.COMPLETED
    assert result.state.node3_output is not None

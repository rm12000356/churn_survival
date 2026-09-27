"""Human mapping-confirmation gate tests (architecture §1.6, ROADMAP Task 7.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from orchestration.graph import resume_pipeline, run_pipeline
from orchestration.mapping import CallbackMappingGate
from orchestration.state import PipelineResult, PipelineStatus
from router.fingerprint import extract_fingerprint
from schemas.mapping import MappingReport
from tests.mapping_helpers import mapping_payload


def _report_for(path: Path) -> MappingReport:
    frame = pd.read_csv(path)
    return MappingReport.model_validate(mapping_payload(extract_fingerprint(frame)))


def test_gate_declines_and_stops(unmapped_csv: Path, tmp_path: Path) -> None:
    seen: list[object] = []

    def decline(report: MappingReport, fingerprint) -> MappingReport | None:
        seen.append((report, fingerprint))
        return None

    result = run_pipeline(
        unmapped_csv,
        mapping_gate=CallbackMappingGate(decline, name="reviewer"),
        mapping_report=_report_for(unmapped_csv),
        config_dir=tmp_path,
    )
    assert result.status is PipelineStatus.STOPPED_NEEDS_MAPPING
    assert len(seen) == 1
    assert not (tmp_path / "mappings").exists()


def test_gate_approves_persists_and_completes(unmapped_csv: Path, tmp_path: Path) -> None:
    calls: list[object] = []

    def approve(report: MappingReport, fingerprint) -> MappingReport | None:
        calls.append(fingerprint)
        return report

    result = run_pipeline(
        unmapped_csv,
        mapping_gate=CallbackMappingGate(approve, name="reviewer"),
        mapping_report=_report_for(unmapped_csv),
        config_dir=tmp_path,
    )
    assert result.status is PipelineStatus.COMPLETED
    assert result.state.mapping_version
    assert len(calls) == 1  # confirmation gate is evaluated exactly once
    mappings = list((tmp_path / "mappings").glob("map_*.json"))
    assert len(mappings) == 1
    assert result.state.node1_output is not None
    assert result.state.node1_output.validation_report.adapter_used.startswith("mapping:")


def test_confirmed_mapping_is_reused_without_a_gate(unmapped_csv: Path, tmp_path: Path) -> None:
    """Cross-request resume: once persisted, a later run needs no gate."""
    first = run_pipeline(
        unmapped_csv,
        mapping_gate=CallbackMappingGate(lambda report, _fp: report, name="reviewer"),
        mapping_report=_report_for(unmapped_csv),
        config_dir=tmp_path,
    )
    assert first.status is PipelineStatus.COMPLETED

    second = run_pipeline(unmapped_csv, config_dir=tmp_path)
    assert second.status is PipelineStatus.COMPLETED
    assert second.state.routing is not None
    assert second.state.routing["matched"] is True
    assert second.state.node1_output is not None
    assert second.state.node1_output.validation_report.adapter_used.startswith("mapping:")


def test_result_round_trip_and_resume(unmapped_csv: Path, tmp_path: Path) -> None:
    stopped = run_pipeline(unmapped_csv, config_dir=tmp_path)
    assert stopped.status is PipelineStatus.STOPPED_NEEDS_MAPPING

    state_path = stopped.save(tmp_path / "state.json")
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert payload["status"] == "STOPPED_NEEDS_MAPPING"
    assert payload["fingerprint"]["headers_hash"]

    loaded = PipelineResult.load(state_path)
    assert loaded.state.raw_path == stopped.state.raw_path
    assert loaded.state.fingerprint == stopped.state.fingerprint

    resumed = resume_pipeline(
        loaded,
        mapping_gate=CallbackMappingGate(lambda report, _fp: report, name="reviewer"),
        mapping_report=_report_for(unmapped_csv),
        config_dir=tmp_path,
    )
    assert resumed.status is PipelineStatus.COMPLETED

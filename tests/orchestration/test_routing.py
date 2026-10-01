"""Routing node tests (architecture §0.1, ROADMAP Task 7.1)."""

from __future__ import annotations

from pathlib import Path

from orchestration.graph import run_pipeline
from orchestration.state import PipelineStage, PipelineStatus


def test_matched_file_routes_and_completes(clean_csv: Path) -> None:
    result = run_pipeline(clean_csv)
    assert result.status is PipelineStatus.COMPLETED
    assert result.state.routing is not None
    assert result.state.routing["matched"] is True
    assert result.state.routing["adapter"] == "clean_csv"
    assert "clean_csv" in result.state.matched_candidates
    assert result.state.fingerprint is not None


def test_unmapped_file_stops_awaiting_confirmation(unmapped_csv: Path) -> None:
    result = run_pipeline(unmapped_csv)
    assert result.status is PipelineStatus.STOPPED_NEEDS_MAPPING
    assert result.state.stage is PipelineStage.MAPPING_CONFIRMATION
    assert result.state.fingerprint is not None
    assert result.state.node1_output is None
    assert result.state.routing is not None
    assert result.state.routing["matched"] is False

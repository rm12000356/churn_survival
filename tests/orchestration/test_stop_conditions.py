"""Explicit stop conditions + partial-state retention (ROADMAP Task 7.2)."""

from __future__ import annotations

from pathlib import Path

from orchestration.graph import run_pipeline
from orchestration.state import PipelineStage, PipelineStatus


def _invalid_batch(path: Path) -> Path:
    """A CSV that matches clean_csv routing but fails validation for every row."""
    path.write_text(
        "customer_id,observation_start,observation_end,event_observed,"
        "plan_tier,contract_length_months,usage_frequency\n"
        "bad_1,2026-08-01,2026-07-01,2,pro,12,10\n"
        "bad_2,2026-08-01,2026-08-15,3,pro,12,10\n",
        encoding="utf-8",
    )
    return path


def test_node1_total_failure_short_circuits(tmp_path: Path) -> None:
    raw = _invalid_batch(tmp_path / "bad_batch.csv")
    result = run_pipeline(raw)
    assert result.status is PipelineStatus.STOPPED_VALIDATION
    assert result.state.node1_output is not None
    assert result.state.node1_output.validation_report.status.value == "FAILED"
    # No downstream node ran.
    assert result.state.node2_output is None
    assert result.state.node3_output is None
    assert result.state.node4_output is None
    assert result.state.node5_output is None


def test_unmapped_without_gate_stops_before_node1(unmapped_csv: Path) -> None:
    result = run_pipeline(unmapped_csv)
    assert result.status is PipelineStatus.STOPPED_NEEDS_MAPPING
    assert result.state.stage is PipelineStage.MAPPING_CONFIRMATION
    assert result.state.node1_output is None


def test_node_exception_fails_but_retains_partial_state(monkeypatch, clean_csv: Path) -> None:
    import node3.node as node3_module

    def _boom(*args, **kwargs):
        raise RuntimeError("synthetic node3 failure")

    monkeypatch.setattr(node3_module, "run_node3", _boom)
    result = run_pipeline(clean_csv)
    assert result.status is PipelineStatus.FAILED
    assert result.state.stage is PipelineStage.NODE3
    # Everything that completed before the failure is retained for debugging.
    assert result.state.node1_output is not None
    assert result.state.node2_output is not None
    assert result.state.node3_output is None
    assert any(error["stage"] == "node3" for error in result.state.errors)


def test_both_upstream_missing_is_impossible_here(clean_csv: Path) -> None:
    """Node 2 always yields an output, so Node 4 never gets an empty pair."""
    result = run_pipeline(clean_csv)
    assert result.status is PipelineStatus.COMPLETED
    assert result.state.node2_output is not None
    assert result.state.node4_output is not None

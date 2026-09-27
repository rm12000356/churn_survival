"""Determinism of the orchestrator (ROADMAP Global Acceptance Bar #1)."""

from __future__ import annotations

from pathlib import Path

from orchestration.graph import run_pipeline


def test_two_runs_are_bit_identical(clean_csv: Path) -> None:
    first = run_pipeline(clean_csv)
    second = run_pipeline(clean_csv)
    assert first.status == second.status
    assert first.state.routing == second.state.routing
    assert first.state.node1_output is not None and second.state.node1_output is not None
    assert (
        first.state.node1_output.model_dump(mode="json")
        == second.state.node1_output.model_dump(mode="json")
    )
    assert first.state.node2_output is not None and second.state.node2_output is not None
    assert (
        first.state.node2_output.model_dump(mode="json")
        == second.state.node2_output.model_dump(mode="json")
    )
    assert first.state.node4_output is not None and second.state.node4_output is not None
    assert (
        first.state.node4_output.model_dump(mode="json")
        == second.state.node4_output.model_dump(mode="json")
    )
    assert first.state.node5_output is not None and second.state.node5_output is not None
    assert (
        first.state.node5_output.model_dump(mode="json")
        == second.state.node5_output.model_dump(mode="json")
    )


def test_summary_is_json_serializable(clean_csv: Path) -> None:
    import json

    result = run_pipeline(clean_csv)
    json.dumps(result.to_dict())
    json.dumps(result.summary())

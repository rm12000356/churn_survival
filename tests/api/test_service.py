"""API service-layer failure-path tests (ROADMAP Phase 8)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from api.service import RunSpec, execute_run
from config.settings import Settings
from orchestration.persistence import RunStore
from orchestration.state import PipelineResult, PipelineState
from schemas.run import RunExecutionStatus, RunSummary


class _Prepared:
    action_rules = None


def _spec(raw_path: Path) -> RunSpec:
    return RunSpec(
        raw_path=str(raw_path),
        node1_version="1",
        node2_version="1",
        node3_version="1",
        node4_version="1",
        node5_version="1",
        action_rules_version="1",
        reference_date=date(2026, 8, 15),
        support_data=None,
        persist_artifact=False,
    )


def test_execute_run_records_failure(
    monkeypatch, tmp_path: Path, api_settings: Settings, clean_csv: Path
) -> None:
    store = RunStore(tmp_path / "runs")
    store.index.upsert(RunSummary(run_id="x", execution_status=RunExecutionStatus.RUNNING))

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr("orchestration.graph.run_pipeline", _boom)
    execute_run(store, api_settings, run_id="x", spec=_spec(clean_csv), prepared=_Prepared())

    summary = store.get_summary("x")
    assert summary is not None
    assert summary.execution_status == RunExecutionStatus.FAILED
    assert summary.error_code == "RuntimeError"


def test_execute_run_without_identity_marks_failed(
    monkeypatch, tmp_path: Path, api_settings: Settings, clean_csv: Path
) -> None:
    store = RunStore(tmp_path / "runs")
    store.index.upsert(RunSummary(run_id="y", execution_status=RunExecutionStatus.RUNNING))
    result = PipelineResult(
        PipelineState(
            raw_path=str(clean_csv),
            reference_date=date(2026, 8, 15),
            errors=[{"code": "ROUTING_FAILED"}],
        )
    )

    monkeypatch.setattr(
        "orchestration.graph.run_pipeline", lambda *_a, **_k: result
    )
    execute_run(store, api_settings, run_id="y", spec=_spec(clean_csv), prepared=_Prepared())

    summary = store.get_summary("y")
    assert summary is not None
    assert summary.execution_status == RunExecutionStatus.FAILED
    assert summary.error_code == "ROUTING_FAILED"


def test_execute_run_reconciles_actual_identity(
    monkeypatch, tmp_path: Path, api_settings: Settings, clean_csv: Path
) -> None:
    """If the computed identity differs, the precomputed row is removed."""
    store = RunStore(tmp_path / "runs")
    store.index.upsert(RunSummary(run_id="z", execution_status=RunExecutionStatus.RUNNING))
    result = PipelineResult(
        PipelineState(
            raw_path=str(clean_csv),
            reference_date=date(2026, 8, 15),
            run_id="different",
        )
    )

    monkeypatch.setattr(
        "orchestration.graph.run_pipeline", lambda *_a, **_k: result
    )
    execute_run(store, api_settings, run_id="z", spec=_spec(clean_csv), prepared=_Prepared())

    assert store.get_summary("z") is None
    assert store.get_summary("different") is not None

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
    node1_version = "1"
    node1_warning = None


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


def test_execute_run_publishes_live_stage_progress(
    monkeypatch, tmp_path: Path, api_settings: Settings, clean_csv: Path
) -> None:
    """The worker mirrors each pipeline stage into the index the UI polls."""
    store = RunStore(tmp_path / "runs")
    store.index.upsert(RunSummary(run_id="live", execution_status=RunExecutionStatus.RUNNING))

    seen: list[str] = []
    real_update = store.index.update_fields

    def _spy(run_id: str, **fields: object) -> bool:
        if "stage" in fields:
            seen.append(str(fields["stage"]))
        return real_update(run_id, **fields)

    monkeypatch.setattr(store.index, "update_fields", _spy)
    execute_run(store, api_settings, run_id="live", spec=_spec(clean_csv), prepared=_Prepared())

    # Routing first (so the UI never looks stuck), and Node 1 reported before
    # Node 3 — which is the stage that can take minutes with a live LLM.
    assert seen and seen[0] == "routing"
    assert "node1" in seen
    assert "node3" in seen
    assert seen.index("node1") < seen.index("node3")


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


def test_client_error_text_keeps_project_messages_and_hides_library_ones() -> None:
    # REVIEW LOW: decided by the raising module, not by a file path.
    from api.service import client_error_text
    from config.loader import load_node1_config

    try:
        load_node1_config("../escape")
    except ValueError as exc:
        project_error = exc
    else:  # pragma: no cover - the loader must reject traversal
        raise AssertionError("expected a ValueError")
    assert client_error_text(project_error) == str(project_error)

    try:
        int("not-a-number-SECRET")
    except ValueError as exc:
        library_error = exc
    text = client_error_text(library_error)
    assert "SECRET" not in text and "ValueError" in text

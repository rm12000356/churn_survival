"""Live stage progress reporting via ``run_pipeline(on_stage=...)``.

The callback is the orchestration's progress hook: the API mirrors it into the
run index so the UI can show which node a run is in, instead of appearing stuck
before Node 1 for the whole run.
"""

from __future__ import annotations

from pathlib import Path

from orchestration.graph import run_pipeline
from orchestration.state import PipelineStage, PipelineStatus


def test_on_stage_reports_every_stage_in_pipeline_order(clean_csv: Path) -> None:
    seen: list[PipelineStage] = []
    result = run_pipeline(clean_csv, on_stage=seen.append)

    assert result.status is PipelineStatus.COMPLETED
    assert seen[0] is PipelineStage.ROUTING
    assert seen[-1] is PipelineStage.DONE
    for stage in (
        PipelineStage.ROUTING,
        PipelineStage.NODE1,
        PipelineStage.NODE2,
        PipelineStage.NODE3,
        PipelineStage.NODE4,
        PipelineStage.NODE5,
        PipelineStage.DONE,
    ):
        assert stage in seen

    order = {stage: index for index, stage in enumerate(PipelineStage)}
    assert seen == sorted(seen, key=order.__getitem__)


def test_failing_on_stage_never_aborts_the_run(clean_csv: Path) -> None:
    """Observability is auxiliary: a broken callback must not fail the pipeline."""

    def _boom(_stage: PipelineStage) -> None:
        raise RuntimeError("progress channel down")

    result = run_pipeline(clean_csv, on_stage=_boom)

    assert result.status is PipelineStatus.COMPLETED
    assert result.state.stage is PipelineStage.DONE

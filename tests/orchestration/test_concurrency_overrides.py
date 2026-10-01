"""REVIEW §5 — NODE{3,5}_LLM_MAX_CONCURRENCY overrides reach the nodes, not the run id."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import node3.node as node3_module
import node5.node as node5_module
from config.settings import get_settings
from orchestration.graph import run_pipeline
from orchestration.state import PipelineStatus


def _capture(monkeypatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}
    original3 = node3_module.run_node3
    original5 = node5_module.run_node5

    def run_node3(customers, support_data, config, **kwargs):
        seen["node3"] = config
        return original3(customers, support_data, config, **kwargs)

    def run_node5(node4_output, config, **kwargs):
        seen["node5"] = config
        return original5(node4_output, config, **kwargs)

    monkeypatch.setattr(node3_module, "run_node3", run_node3)
    monkeypatch.setattr(node5_module, "run_node5", run_node5)
    return seen


def test_overrides_apply_and_keep_run_identity(monkeypatch, clean_csv: Path) -> None:
    seen = _capture(monkeypatch)
    baseline = run_pipeline(clean_csv)
    assert baseline.status is PipelineStatus.COMPLETED
    default3 = seen["node3"].llm_max_concurrency
    default5 = seen["node5"].llm_max_concurrency

    settings = get_settings().model_copy(
        update={"NODE3_LLM_MAX_CONCURRENCY": 17, "NODE5_LLM_MAX_CONCURRENCY": 3}
    )
    overridden = run_pipeline(clean_csv, settings=settings)
    assert overridden.status is PipelineStatus.COMPLETED
    assert seen["node3"].llm_max_concurrency == 17 != default3
    assert seen["node5"].llm_max_concurrency == 3 != default5

    assert overridden.state.run_id == baseline.state.run_id
    assert overridden.state.config_versions == baseline.state.config_versions
    assert (
        overridden.state.node5_output.model_dump_json()
        == baseline.state.node5_output.model_dump_json()
    )

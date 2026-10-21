"""Per-run LLM selection: off by default, opt-in per node, part of the run identity."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.service import RunSpec, execute_run, prepare_run
from config.loader import load_node5_config
from config.settings import Settings
from orchestration.graph import _select_llm_nodes
from orchestration.persistence import RunStore
from schemas.run import RunExecutionStatus, RunSummary


class _FakeLlm:
    provider = "fake"
    model = "fake-model"


class _Prepared:
    action_rules = None
    node1_version = "1"
    node1_warning = None


def _llm_settings(api_settings: Settings) -> Settings:
    return api_settings.model_copy(
        update={"LLM_PROVIDER": "openai", "LLM_API_KEY": "k", "LLM_MODEL": "fake-model"}
    )


def _spec(raw_path: Path, llm_nodes: frozenset[str] = frozenset()) -> RunSpec:
    return RunSpec(
        raw_path=str(raw_path),
        node1_version="1",
        node2_version="1",
        node3_version="1",
        node4_version="2",
        node5_version="1",
        action_rules_version="1",
        reference_date=date(2026, 8, 15),
        support_data=None,
        persist_artifact=False,
        llm_nodes=llm_nodes,
    )


# --- orchestration: routing the client to the selected nodes ------------------


def test_legacy_selection_passes_client_to_both_nodes_without_identity() -> None:
    client = _FakeLlm()
    config = load_node5_config("1")
    n3, n5, cfg, ident = _select_llm_nodes(client, None, config)
    assert (n3, n5, ident) == (client, client, None)
    assert cfg is config


def test_empty_selection_disables_the_llm_everywhere() -> None:
    n3, n5, cfg, ident = _select_llm_nodes(_FakeLlm(), frozenset(), load_node5_config("1"))
    assert (n3, n5, ident) == (None, None, None)
    assert cfg.llm_enabled is False


def test_node5_only_selection_enables_polish_and_skips_node3() -> None:
    client = _FakeLlm()
    n3, n5, cfg, ident = _select_llm_nodes(client, {"node5"}, load_node5_config("1"))
    assert n3 is None and n5 is client
    assert cfg.llm_enabled is True
    assert ident == "node5"


def test_node3_only_selection_turns_node5_polish_off() -> None:
    client = _FakeLlm()
    config = load_node5_config("1").model_copy(update={"llm_enabled": True})
    n3, n5, cfg, ident = _select_llm_nodes(client, {"node3"}, config)
    assert n3 is client and n5 is None
    assert cfg.llm_enabled is False
    assert ident == "node3"


def test_unknown_llm_node_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown llm_nodes"):
        _select_llm_nodes(_FakeLlm(), {"node4"}, load_node5_config("1"))


# --- API ------------------------------------------------------------------------


def test_llm_request_without_configured_llm_is_422(client: TestClient, raw_dir: Path) -> None:
    for flag in ("llm_node3", "llm_node5"):
        response = client.post(
            "/runs", json={"raw_path": str(raw_dir / "clean_customers.csv"), flag: True}
        )
        assert response.status_code == 422, flag
        assert "LLM_PROVIDER=none" in response.json()["detail"]


def test_status_reports_llm_availability(make_client, api_settings: Settings) -> None:
    assert make_client(api_settings).get("/status").json()["llm_available"] is False
    body = make_client(_llm_settings(api_settings)).get("/status").json()
    assert body["llm_available"] is True
    assert body["llm_model"] == "fake-model"


def test_each_llm_choice_is_a_distinct_run_and_default_is_llm_free(
    monkeypatch, api_settings: Settings, clean_csv: Path
) -> None:
    monkeypatch.setattr("api.service.llm_client_or_none", lambda _s: _FakeLlm())
    llm_settings = _llm_settings(api_settings)
    spec = _spec(clean_csv)
    ids = {
        nodes: prepare_run(llm_settings, replace(spec, llm_nodes=frozenset(nodes))).run_id
        for nodes in ((), ("node3",), ("node5",), ("node3", "node5"))
    }
    assert len(set(ids.values())) == 4
    # The default (nothing selected) is the same run whether or not an LLM is configured.
    monkeypatch.setattr("api.service.llm_client_or_none", lambda _s: None)
    assert prepare_run(api_settings, spec).run_id == ids[()]


def test_worker_passes_client_only_when_selected(
    monkeypatch, tmp_path: Path, api_settings: Settings, clean_csv: Path
) -> None:
    captured: list[dict[str, Any]] = []

    def _capture(*_args: object, **kwargs: Any) -> None:
        captured.append(kwargs)
        raise RuntimeError("stop after capture")

    monkeypatch.setattr("orchestration.graph.run_pipeline", _capture)
    monkeypatch.setattr("api.service.llm_client_or_none", lambda _s: _FakeLlm())
    store = RunStore(tmp_path / "runs")
    for run_id, nodes in (("a", frozenset()), ("b", frozenset({"node5"}))):
        store.index.upsert(RunSummary(run_id=run_id, execution_status=RunExecutionStatus.RUNNING))
        execute_run(
            store,
            api_settings,
            run_id=run_id,
            spec=_spec(clean_csv, nodes),
            prepared=_Prepared(),  # type: ignore[arg-type]
        )
    assert captured[0]["llm_client"] is None
    assert captured[0]["llm_nodes"] == frozenset()
    assert isinstance(captured[1]["llm_client"], _FakeLlm)
    assert captured[1]["llm_nodes"] == frozenset({"node5"})

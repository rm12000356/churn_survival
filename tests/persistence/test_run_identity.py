"""Run identity tests (ROADMAP Phase 8, D-P1)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from config.loader import load_node1_config
from orchestration.identity import compute_run_id
from orchestration.persistence import compute_trigger_run_id
from orchestration.routing import build_adapters
from schemas.run import RoutingIdentity

REFERENCE_DATE = date(2026, 8, 15)


def _config_versions() -> dict[str, str]:
    return {"node1": "1", "node2": "1", "node3": "1", "node4": "1", "node5": "1"}


def test_same_identity_same_run_id() -> None:
    kwargs = {
        "raw_digest": "abc",
        "support_digest": "def",
        "config_versions": _config_versions(),
        "reference_date": REFERENCE_DATE,
        "routing_identity": RoutingIdentity(matched=True, adapter="clean_csv", adapter_version="1"),
    }
    assert compute_run_id(**kwargs) == compute_run_id(**kwargs)


def test_routing_identity_changes_run_id() -> None:
    shared = {
        "raw_digest": "abc",
        "support_digest": "def",
        "config_versions": _config_versions(),
        "reference_date": REFERENCE_DATE,
    }
    unmatched = compute_run_id(**shared, routing_identity=RoutingIdentity.no_match())
    matched = compute_run_id(
        **shared,
        routing_identity=RoutingIdentity(matched=True, adapter="clean_csv", adapter_version="1"),
    )
    assert unmatched != matched


def test_config_version_changes_run_id() -> None:
    shared = {
        "raw_digest": "abc",
        "support_digest": "def",
        "reference_date": REFERENCE_DATE,
        "routing_identity": RoutingIdentity.no_match(),
    }
    a = compute_run_id(**shared, config_versions={"node1": "1"})
    b = compute_run_id(**shared, config_versions={"node1": "2"})
    assert a != b


def test_trigger_run_id_is_decision_free(monkeypatch: pytest.MonkeyPatch, clean_csv: Path) -> None:
    """Computing a trigger id must never execute any node (D-P1)."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("node execution is not allowed while forming a run identity")

    import node1.node as node1
    import node2.node as node2
    import node3.node as node3
    import node4.node as node4
    import node5.node as node5

    monkeypatch.setattr(node1, "run_node1", _boom)
    monkeypatch.setattr(node2, "run_node2", _boom)
    monkeypatch.setattr(node2, "fit_model", _boom)
    monkeypatch.setattr(node2, "score_to_output", _boom)
    monkeypatch.setattr(node3, "run_node3", _boom)
    monkeypatch.setattr(node4, "run_node4", _boom)
    monkeypatch.setattr(node5, "run_node5", _boom)

    run_id, routing = compute_trigger_run_id(
        clean_csv,
        node1_config=load_node1_config("1"),
        adapters=build_adapters(),
        config_versions=_config_versions(),
        reference_date=REFERENCE_DATE,
    )
    assert run_id
    assert routing.matched


def test_trigger_run_id_is_stable_across_rebuilds(clean_csv: Path) -> None:
    """Same fingerprint against the same registry -> identical routing identity.

    This is the property the index self-heal implicitly depends on: routing
    must be stable across process restarts.
    """
    kwargs = {
        "node1_config": load_node1_config("1"),
        "config_versions": _config_versions(),
        "reference_date": REFERENCE_DATE,
    }
    first_id, first_routing = compute_trigger_run_id(
        clean_csv, adapters=build_adapters(), **kwargs
    )
    second_id, second_routing = compute_trigger_run_id(
        clean_csv, adapters=build_adapters(), **kwargs
    )
    assert first_id == second_id
    assert first_routing == second_routing

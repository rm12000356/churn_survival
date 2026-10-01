"""A file is routed once and its adapter is reused to the end of the run.

Routing (fingerprint + adapter selection) used to re-read and re-route the same
raw file at every step: API trigger, Node 1 config resolution, orchestrator
routing, and again inside Node 1. The fingerprint is now cached per file version
and Node 1 reuses the decision the orchestrator already made.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

import node1.node as node1_module
import orchestration.routing as routing
from config.loader import load_node1_config
from node1.node import run_node1


@pytest.fixture
def raw_copy(clean_csv: Path, tmp_path: Path) -> Path:
    target = tmp_path / "customers.csv"
    shutil.copyfile(clean_csv, target)
    return target


def test_fingerprint_is_parsed_once_per_file_version(
    raw_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    original = routing.fingerprint_input

    def counting(path: str | Path) -> object:
        calls.append(Path(path))
        return original(path)

    monkeypatch.setattr(routing, "fingerprint_input", counting)

    first = routing.fingerprint_file(raw_copy)
    second = routing.fingerprint_file(raw_copy)
    assert first == second
    assert len(calls) == 1

    # Changing the file invalidates the cached fingerprint.
    raw_copy.write_text(raw_copy.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    stat = raw_copy.stat()
    os.utime(raw_copy, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    routing.fingerprint_file(raw_copy)
    assert len(calls) == 2


def test_cached_fingerprint_is_a_copy(raw_copy: Path) -> None:
    first = routing.fingerprint_file(raw_copy)
    first.column_names.append("tampered")
    assert "tampered" not in routing.fingerprint_file(raw_copy).column_names


def test_node1_reuses_the_orchestrator_decision(
    raw_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = load_node1_config("1")
    _fingerprint, decision = routing.route_input(raw_copy, config)
    assert decision.matched

    def fail_route(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("Node 1 re-routed a file that was already routed")

    monkeypatch.setattr(node1_module, "route", fail_route)
    reused = run_node1(raw_copy, config=config, decision=decision)
    assert reused.validation_report.adapter_used == decision.adapter.name


def test_node1_reroutes_when_decision_does_not_fit_the_file(
    raw_copy: Path, unmapped_csv: Path
) -> None:
    config = load_node1_config("1")
    _fingerprint, decision = routing.route_input(raw_copy, config)
    # A decision made for a different file shape must not be applied blindly.
    with pytest.raises(node1_module.UnmappedFormatError):
        run_node1(unmapped_csv, config=config, decision=decision)

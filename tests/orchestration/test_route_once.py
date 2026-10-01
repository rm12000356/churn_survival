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


# --- REVIEW N-M13 / T2 ---------------------------------------------------------


def test_same_size_header_change_with_restored_mtime_is_detected(raw_copy: Path) -> None:
    first = routing.fingerprint_file(raw_copy)
    stat = raw_copy.stat()
    data = raw_copy.read_bytes()
    # Same length, different header: swap the first two bytes of the first column.
    renamed = data[1:2] + data[0:1] + data[2:]
    assert renamed != data and len(renamed) == len(data)
    raw_copy.write_bytes(renamed)
    os.utime(raw_copy, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # restored mtime
    assert raw_copy.stat().st_size == stat.st_size
    second = routing.fingerprint_file(raw_copy)
    assert second.headers_hash != first.headers_hash


def test_cache_evicts_lru_beyond_capacity(
    clean_csv: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routing, "_FINGERPRINT_CACHE", routing.OrderedDict())
    files = []
    for i in range(routing._FINGERPRINT_CACHE_SIZE + 2):
        target = tmp_path / f"c{i}.csv"
        shutil.copyfile(clean_csv, target)
        files.append(target)
        routing.fingerprint_file(target)
    assert len(routing._FINGERPRINT_CACHE) == routing._FINGERPRINT_CACHE_SIZE
    cached_paths = {key[0] for key in routing._FINGERPRINT_CACHE}
    assert str(files[0].resolve()) not in cached_paths  # oldest evicted
    assert str(files[-1].resolve()) in cached_paths


def test_fingerprint_file_is_thread_safe(raw_copy: Path) -> None:
    import threading

    results: list[str] = []
    barrier = threading.Barrier(8)

    def work() -> None:
        barrier.wait()
        results.append(routing.fingerprint_file(raw_copy).headers_hash)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 8 and len(set(results)) == 1


def test_auto_resolve_without_default_config_keeps_a_real_threshold(
    raw_copy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # REVIEW LOW: a missing default config used to route with threshold 0.0
    # (accept anything) silently.
    import config.loader as loader

    def missing(_version: str) -> object:
        raise FileNotFoundError("no such config")

    seen: list[float] = []
    original_route = routing.route

    def spy(fingerprint, candidates, *, high_confidence_threshold):  # type: ignore[no-untyped-def]
        seen.append(high_confidence_threshold)
        return original_route(
            fingerprint, candidates, high_confidence_threshold=high_confidence_threshold
        )

    monkeypatch.setattr(loader, "load_node1_config", missing)
    monkeypatch.setattr(routing, "route", spy)
    version, warning = routing.resolve_node1_version(raw_copy, "auto", adapters=[])
    assert version == "1"
    assert seen and seen[0] > 0.0
    assert warning and "default confidence threshold" in warning

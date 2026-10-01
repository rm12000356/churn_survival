"""Reproducibility audit tests (ROADMAP Task 9.3).

Covers the three skip/fail attribution paths plus a clean CSV-only pass:
PASS, MAPPING_CHANGED (routing pre-flight short-circuit), MISSING_SUPPORT_INPUTS,
and a genuine byte-diff FAIL that is NOT attributed to a mapping change.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from orchestration.graph import run_pipeline
from orchestration.persistence import RunStore
from scripts.audit_reproducibility import audit_run
from tests.e2e.conftest import (
    DATASET7_CSV,
    DATASET7_THREADS,
    DATASET7_VERSIONS,
    REFERENCE_DATE,
)


def _threads() -> list:
    return json.loads(DATASET7_THREADS.read_text(encoding="utf-8"))


def test_audit_passes_for_dataset7(
    seed_dataset7, e2e_settings, store: RunStore
) -> None:
    result = seed_dataset7()
    outcome = audit_run(
        store,
        result.state.run_id,
        support_data=_threads(),
        config_dir=e2e_settings.CONFIG_DIR,
        settings=e2e_settings,
    )
    assert outcome.result == "PASS", outcome
    assert outcome.mismatches == []


def test_audit_reports_mapping_changed(
    seed_dataset7, e2e_settings, store: RunStore, monkeypatch
) -> None:
    result = seed_dataset7()
    mappings = Path(e2e_settings.CONFIG_DIR) / "mappings"
    for mapping in mappings.glob("map_*.json"):
        mapping.unlink()

    # Prove the short-circuit fires before the expensive full re-run.
    import scripts.audit_reproducibility as audit_mod

    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("run_pipeline must not be called on MAPPING_CHANGED")

    monkeypatch.setattr(audit_mod, "run_pipeline", _boom)

    outcome = audit_run(
        store,
        result.state.run_id,
        support_data=_threads(),
        config_dir=e2e_settings.CONFIG_DIR,
        settings=e2e_settings,
    )
    assert outcome.result == "MAPPING_CHANGED"
    assert "recorded=" in outcome.detail and "current=" in outcome.detail
    assert outcome.mismatches == []


def test_audit_reports_missing_support_inputs(
    seed_dataset7, e2e_settings, store: RunStore
) -> None:
    result = seed_dataset7()
    outcome = audit_run(
        store,
        result.state.run_id,
        config_dir=e2e_settings.CONFIG_DIR,
        settings=e2e_settings,
    )
    assert outcome.result == "MISSING_SUPPORT_INPUTS"
    assert outcome.mismatches == []


def test_audit_handles_cli_style_model_inputs(
    e2e_settings, store: RunStore
) -> None:
    """A run whose support input was ``SupportThread`` models (the CLI path) is
    reproduced even when the audit re-supplies raw dicts (form normalization)."""
    from schemas.node3 import SupportThread

    threads = _threads()
    result = run_pipeline(
        DATASET7_CSV,
        support_data=[SupportThread.model_validate(entry) for entry in threads],
        action_rules=None,
        reference_date=REFERENCE_DATE,
        settings=e2e_settings,
        config_dir=e2e_settings.CONFIG_DIR,
        **DATASET7_VERSIONS,
    )
    assert result.state.run_id is not None
    store.save(result)

    outcome = audit_run(
        store,
        result.state.run_id,
        support_data=threads,  # raw dicts, as the audit CLI loads them
        config_dir=e2e_settings.CONFIG_DIR,
        settings=e2e_settings,
    )
    assert outcome.result == "PASS", outcome


def test_audit_fails_on_corrupted_output(
    seed_dataset7, e2e_settings, store: RunStore
) -> None:
    result = seed_dataset7()
    run_id = result.state.run_id
    node4 = store.run_dir(run_id) / "node4.json"
    payload = json.loads(node4.read_text(encoding="utf-8"))
    payload["summary_stats"]["n_customers"] += 1
    node4.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    outcome = audit_run(
        store,
        run_id,
        support_data=_threads(),
        config_dir=e2e_settings.CONFIG_DIR,
        settings=e2e_settings,
    )
    assert outcome.result == "FAIL"
    assert any("node4.json" in mismatch for mismatch in outcome.mismatches)


def test_audit_passes_for_csv_only_run(
    clean_csv: Path, e2e_settings, store: RunStore
) -> None:
    result = run_pipeline(
        clean_csv,
        reference_date=date(2026, 8, 15),
        settings=e2e_settings,
        config_dir=e2e_settings.CONFIG_DIR,
    )
    assert result.state.run_id is not None
    store.save(result)

    outcome = audit_run(
        store,
        result.state.run_id,
        config_dir=e2e_settings.CONFIG_DIR,
        settings=e2e_settings,
    )
    assert outcome.result == "PASS", outcome


def test_audit_versions_roundtrip_uses_dataset7_versions(
    seed_dataset7, e2e_settings, store: RunStore
) -> None:
    """A run using deployment versions audits with those versions reconstructed."""
    result = seed_dataset7()
    assert result.state.config_versions["node1"] == DATASET7_VERSIONS["node1_version"]
    assert result.state.reference_date == REFERENCE_DATE


def _with_versions(store: RunStore, run_id: str, **changes: str) -> None:
    persisted = store.load(run_id)
    persisted.state.config_versions.update(changes)
    store.save(persisted)


def test_audit_skips_runs_from_older_code_semantics(
    seed_dataset7, e2e_settings, store: RunStore, monkeypatch
) -> None:
    # REVIEW N-H3: a run computed by older code is expected to differ.
    import scripts.audit_reproducibility as audit_mod

    result = seed_dataset7()
    _with_versions(store, result.state.run_id, semantics="2000.01.1")
    monkeypatch.setattr(audit_mod, "run_pipeline", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("no re-run for a semantics change")
    ))
    outcome = audit_run(
        store, result.state.run_id, config_dir=e2e_settings.CONFIG_DIR, settings=e2e_settings
    )
    assert outcome.result == "SEMANTICS_CHANGED"


def test_audit_skips_llm_runs(seed_dataset7, e2e_settings, store: RunStore) -> None:
    # REVIEW LOW: LLM text is not byte-reproducible; never report it as a FAIL.
    result = seed_dataset7()
    _with_versions(store, result.state.run_id, llm="openai:gpt-x")
    outcome = audit_run(
        store, result.state.run_id, config_dir=e2e_settings.CONFIG_DIR, settings=e2e_settings
    )
    assert outcome.result == "LLM_ENABLED"

"""Reproducibility audit (ROADMAP Task 9.3, architecture §6, §4.27).

Proves that a persisted run can be recomputed bit-for-bit: it re-invokes
``run_pipeline`` with the original raw file, config versions and declared
``reference_date``, then diffs every persisted output (``node1..node5.json`` and
``report.html``). Non-zero exit on any byte mismatch.

Two checks short-circuit *before* the expensive re-run (both are skips, not
failures):

- **``MAPPING_CHANGED``** — the run identity includes the resolved routing
  identity (adapter + version) against the *current* adapter registry (Phase 8,
  D-P1). A cheap, decision-free fingerprint + route pass is compared with the
  identity recorded in ``state.json``; a mismatch means the mapping registry
  changed since the run (D-H9) and is reported specifically instead of as a wall
  of downstream byte mismatches.
- **``MISSING_SUPPORT_INPUTS``** — only ``support_digest`` is persisted, not the
  raw Node 3 inputs (privacy, D-H7). Callers re-supply ``--threads``/``--sources``
  and the recomputed digest must match. The digest is form-sensitive (plain
  dicts vs ``SupportThread`` models), so the audit tries both in-memory forms and
  reproduces with whichever matches — CLI- and API-created runs are both handled.

An in-place mapping mutation that keeps the same adapter name/version leaves the
routing identity unchanged, so the pre-flight cannot attribute it; the byte diff
then reports ``FAIL`` (the run genuinely no longer reproduces).

Usage:
    python scripts/audit_reproducibility.py --run-id <id> [--threads t.json]
    python scripts/audit_reproducibility.py --all --threads t.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from config.loader import (  # noqa: E402
    load_action_rules,
    load_identity_mapping,
    load_node1_config,
    load_node3_sources_config,
)
from config.settings import Settings, get_settings  # noqa: E402
from orchestration.graph import run_pipeline  # noqa: E402
from orchestration.identity import compute_support_digest  # noqa: E402
from orchestration.persistence import RunStore  # noqa: E402
from orchestration.routing import build_adapters, route_input, routing_identity  # noqa: E402

AuditStatus = Literal["PASS", "FAIL", "MISSING_SUPPORT_INPUTS", "MAPPING_CHANGED"]

_NODE_NAMES = ("node1", "node2", "node3", "node4")


@dataclass
class AuditOutcome:
    """Result of auditing a single persisted run."""

    run_id: str
    result: AuditStatus
    mismatches: list[str] = field(default_factory=list)
    detail: str = ""

    def _dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "result": self.result,
            "mismatches": self.mismatches,
            "detail": self.detail,
        }


def _settings_for(reference_date: date) -> Settings:
    try:
        return get_settings()
    except Exception:  # noqa: BLE001 - the audit must not require a runtime REFERENCE_DATE
        return Settings(REFERENCE_DATE=reference_date)


def _reproduce_node_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _candidate_forms(items: Sequence[Any] | None) -> list[Sequence[Any] | None]:
    """In-memory forms a support input may have had at run time.

    The digest is canonical *per form*: dicts (``POST /runs`` / raw
    ``run_pipeline``) and ``SupportThread`` models (``churn-survival run``)
    hash differently for the same JSON, so the audit tries both.
    """
    if items is None:
        return [None]
    forms: list[Sequence[Any] | None] = [items]
    if any(isinstance(item, dict) for item in items):
        from schemas.node3 import SupportThread

        try:
            normalized: list[Any] = [
                SupportThread.model_validate(item) if isinstance(item, dict) else item
                for item in items
            ]
        except Exception:  # noqa: BLE001 - unusable as a model form; keep the raw form
            return forms
        forms.append(normalized)
    return forms


def audit_run(
    store: RunStore,
    run_id: str,
    *,
    support_data: Sequence[Any] | None = None,
    external_threads: Sequence[Any] | None = None,
    sources_config: Any | None = None,
    identity_mapping: Any | None = None,
    config_dir: str | Path | None = None,
    settings: Settings | None = None,
) -> AuditOutcome:
    """Audit one persisted run; never raises (a failure is a structured outcome)."""
    try:
        state = store.load(run_id).state
    except Exception as exc:  # noqa: BLE001 - unknown / unreadable run
        return AuditOutcome(run_id, "FAIL", [f"cannot load persisted run: {exc}"], "load error")
    versions = state.config_versions
    node1_version = versions.get("node1", "1")

    # --- D-H9: routing pre-flight (cheap, decision-free) ---------------------
    try:
        node1_config = load_node1_config(node1_version)
        adapters = build_adapters(config_dir)
        _, decision = route_input(state.raw_path, node1_config, adapters=adapters)
        current = routing_identity(decision)
    except Exception as exc:  # noqa: BLE001 - cannot reproduce if we cannot even route
        return AuditOutcome(
            run_id, "FAIL", [f"routing pre-check failed: {exc}"], "pre-flight error"
        )

    recorded = state.routing_identity
    if recorded is not None and current != recorded:
        detail = f"recorded={recorded.model_dump()} current={current.model_dump()}"
        return AuditOutcome(run_id, "MAPPING_CHANGED", [], detail)

    # --- D-H7: support-input gate --------------------------------------------
    # The digest is canonical from the *in-memory form* of the inputs, and the
    # two real ingestion paths differ: ``churn-survival run`` validates threads
    # into ``SupportThread`` models, while ``POST /runs`` / raw ``run_pipeline``
    # pass plain dicts. Try both forms and reproduce with whichever matches the
    # stored digest (identity is unchanged; D-H7 semantics are preserved).
    sources_version = sources_config.sources_version if sources_config is not None else None
    identity_version = (
        identity_mapping.mapping_version if identity_mapping is not None else None
    )
    chosen_support: Sequence[Any] | None = support_data
    chosen_external: Sequence[Any] | None = external_threads
    matched = False
    for candidate_support in _candidate_forms(support_data):
        for candidate_external in _candidate_forms(external_threads):
            digest = compute_support_digest(
                support_data=candidate_support,
                external_threads=candidate_external,
                sources_config_version=sources_version,
                identity_mapping_version=identity_version,
            )
            if not state.support_digest or digest == state.support_digest:
                chosen_support, chosen_external, matched = (
                    candidate_support,
                    candidate_external,
                    True,
                )
                break
        if matched:
            break
    if state.support_digest and not matched:
        return AuditOutcome(
            run_id,
            "MISSING_SUPPORT_INPUTS",
            [],
            "recomputed support digest != stored; re-supply --threads/--sources",
        )

    # --- Full re-run ----------------------------------------------------------
    action_rules = None
    if (action_rules_version := versions.get("action_rules")) is not None:
        try:
            action_rules = load_action_rules(action_rules_version)
        except Exception:  # noqa: BLE001 - fall back to the graph's default resolution
            action_rules = None
    resolved_settings = settings or _settings_for(state.reference_date)
    try:
        new = run_pipeline(
            Path(state.raw_path),
            node1_version=node1_version,
            node2_version=versions.get("node2", "1"),
            node3_version=versions.get("node3", "1"),
            node4_version=versions.get("node4", "1"),
            node5_version=versions.get("node5", "1"),
            support_data=chosen_support,
            external_threads=chosen_external,
            sources_config=sources_config,
            identity_mapping=identity_mapping,
            action_rules=action_rules,
            settings=resolved_settings,
            config_dir=config_dir,
            reference_date=state.reference_date,
        )
    except Exception as exc:  # noqa: BLE001 - structured failure
        return AuditOutcome(run_id, "FAIL", [f"re-run failed: {exc}"], "re-run error")

    # --- Diff outputs ---------------------------------------------------------
    mismatches: list[str] = []
    if new.state.run_id != state.run_id:
        mismatches.append(
            f"run_id: stored={state.run_id} recomputed={new.state.run_id}"
        )
    for node in _NODE_NAMES:
        persisted = store.run_dir(run_id) / f"{node}.json"
        if not persisted.is_file():
            continue
        model = getattr(new.state, f"{node}_output")
        if model is None:
            mismatches.append(f"{node}.json: absent in re-run")
            continue
        expected = _reproduce_node_json(model.model_dump(mode="json"))
        if persisted.read_text(encoding="utf-8") != expected:
            mismatches.append(f"{node}.json: byte mismatch")

    node5_path = store.run_dir(run_id) / "node5.json"
    if node5_path.is_file():
        from node5.rendering.json import render_json

        if new.state.node5_output is None:
            mismatches.append("node5.json: absent in re-run")
        elif node5_path.read_text(encoding="utf-8") != (
            render_json(new.state.node5_output) + "\n"
        ):
            mismatches.append("node5.json: byte mismatch")

    persisted_html = store.read_report_html(run_id)
    if persisted_html is not None:
        from node5.rendering.html import render_html

        if new.state.node5_output is None or render_html(new.state.node5_output) != persisted_html:
            mismatches.append("report.html: byte mismatch")

    if mismatches:
        return AuditOutcome(run_id, "FAIL", mismatches, "byte diff")
    return AuditOutcome(run_id, "PASS", [], "bit-identical")


def _load_threads(path: str | None) -> list[Any] | None:
    if path is None:
        return None
    loaded: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(loaded, list):
        raise ValueError("threads file must contain a JSON list of thread objects")
    return loaded


def _load_sources(options: dict[str, str | None]) -> tuple[Any | None, Any | None]:
    spec = options["--sources"]
    if not spec:
        return None, None
    from node3.node import _select_sources

    settings = get_settings()
    base_sources = load_node3_sources_config(str(options["--sources-config"]))
    sources_config = _select_sources(
        base_sources,
        spec,
        options["--source-mode"],
        default_mode=settings.NODE3_SOURCE_MODE,
    )
    identity_version = options["--identity-map"] or base_sources.identity_mapping_version
    identity_mapping = load_identity_mapping(str(identity_version))
    return sources_config, identity_mapping


def _run_ids(store: RunStore, run_id: str | None, all_runs: bool) -> list[str]:
    if run_id:
        return [run_id]
    assert all_runs  # noqa: S101 - guaranteed by argparse mutually-exclusive group
    return [summary.run_id for summary in store.list_runs(limit=None)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reproducibility audit")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--run-id", help="audit a single persisted run")
    group.add_argument("--all", action="store_true", help="audit every persisted run")
    parser.add_argument(
        "--run-dir", default=None, help="run store root (default: settings.RUN_DIR)"
    )
    parser.add_argument("--threads", default=None, help="support threads JSON to re-supply")
    parser.add_argument("--sources", default=None, help="external sources spec (x,gmail|mock)")
    parser.add_argument("--source-mode", default=None, help="mock|live")
    parser.add_argument("--sources-config", default="1")
    parser.add_argument("--identity-map", default=None)
    parser.add_argument("--config-dir", default=None)
    parser.add_argument("--json", action="store_true", help="emit machine-readable results")
    args = parser.parse_args(argv)

    base_dir = Path(args.run_dir) if args.run_dir else get_settings().RUN_DIR
    store = RunStore(base_dir)

    try:
        support_data = _load_threads(args.threads)
        sources_config, identity_mapping = _load_sources(
            {
                "--sources": args.sources,
                "--source-mode": args.source_mode,
                "--sources-config": args.sources_config,
                "--identity-map": args.identity_map,
            }
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        print(f"ERROR: could not load support inputs: {exc}", file=sys.stderr)
        return 2

    run_ids = _run_ids(store, args.run_id, args.all)
    if not run_ids:
        print("No persisted runs found.", file=sys.stderr)
        return 0

    outcomes: list[AuditOutcome] = []
    for candidate in run_ids:
        outcome = audit_run(
            store,
            candidate,
            support_data=support_data,
            sources_config=sources_config,
            identity_mapping=identity_mapping,
            config_dir=args.config_dir,
        )
        outcomes.append(outcome)

    if args.json:
        print(json.dumps([o._dict() for o in outcomes], indent=2))
    else:
        print("Reproducibility audit")
        for outcome in outcomes:
            line = f"  {outcome.run_id}  {outcome.result}"
            if outcome.detail:
                line += f"  ({outcome.detail})"
            print(line)
            for mismatch in outcome.mismatches:
                print(f"      - {mismatch}")
        n_pass = sum(1 for o in outcomes if o.result == "PASS")
        n_skip = sum(
            1 for o in outcomes if o.result in {"MAPPING_CHANGED", "MISSING_SUPPORT_INPUTS"}
        )
        n_fail = sum(1 for o in outcomes if o.result == "FAIL")
        print(f"  pass={n_pass} skip={n_skip} fail={n_fail}")

    return 1 if any(o.result == "FAIL" for o in outcomes) else 0


if __name__ == "__main__":
    sys.exit(main())

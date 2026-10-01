"""CLI for ``churn-survival run`` (ROADMAP Phase 7).

Runs the full pipeline end-to-end: routing -> (optional human mapping gate) ->
Node 1 -> Node 2 -> (optional Node 3) -> Node 4 -> Node 5. The raw file is the
only required argument. Node 3 inputs are optional: omit them for a
quantitative-only synthesis.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from config.loader import (
    load_identity_mapping,
    load_node3_sources_config,
    load_vocabulary,
)
from config.settings import get_settings
from orchestration.graph import run_pipeline
from orchestration.mapping import CallbackMappingGate
from orchestration.state import PipelineResult, PipelineStatus
from schemas.mapping import MappingReport
from schemas.node3 import SupportThread

_FLAG_DEFAULTS: dict[str, str | None] = {
    "--node1": "auto",
    "--node2": "1",
    "--node3": "1",
    "--node4": "4",
    "--node5": "1",
    "--threads": None,
    "--sources": None,
    "--source-mode": None,
    "--sources-config": "1",
    "--identity-map": None,
    "--mapping": None,
    "--node1-config": None,
    "--action-rules": "1",
    "--output": None,
    "--run-dir": None,
    "--mock-dir": None,
}


def _usage() -> str:
    return (
        "churn-survival run <raw-file> "
        "[--node1 <v|auto>] [--node2 <v>] [--node3 <v>] [--node4 <v>] [--node5 <v>] "
        "[--threads <threads.json>] [--sources <x,gmail|mock>] "
        "[--sources-config <v>] [--identity-map <v>] [--source-mode mock|live] "
        "[--mapping <draft.json> --confirm-mapping [--node1-config <v>]] "
        "[--action-rules <v>] "
        "[--persist-artifact] [--persist-run] [--run-dir <path>] [--output <result.json>]"
    )


def _parse_flags(args: list[str]) -> tuple[dict[str, str | None], list[str]]:
    options = dict(_FLAG_DEFAULTS)
    positional: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token in options:
            if index + 1 >= len(args):
                raise ValueError(f"missing value for {token}")
            options[token] = args[index + 1]
            index += 2
        else:
            positional.append(token)
            index += 1
    return options, positional


def _load_support_data(path: str | None) -> list[SupportThread] | None:
    if path is None:
        return None
    loaded: Any = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(loaded, list):
        raise ValueError("threads file must contain a JSON list of thread objects")
    return [SupportThread.model_validate(entry) for entry in loaded]


def _print_summary(result: PipelineResult) -> None:
    summary = result.summary()
    print(
        f"Run: status={summary['status']} stage={summary['stage']} "
        f"adapter={summary['adapter_used']} "
        f"accepted={summary['n_accepted']} rejected={summary['n_rejected']}"
    )
    if summary["model_type"] is not None:
        print(
            f"Run: model={summary['model_type']} version={summary['model_version']} "
            f"ranked={summary['n_ranked']} insufficient={summary['n_insufficient']} "
            f"reports={summary['n_reports']}"
        )
    if summary["mapping_version"]:
        print(f"Run: confirmed mapping={summary['mapping_version']}")
    if summary["artifact_dir"]:
        print(f"Run: artifact persisted -> {summary['artifact_dir']}")
    for warning in summary["warnings"]:
        print(f"Run: warning: {warning}", file=sys.stderr)
    for error in summary["errors"]:
        print(f"Run: error: {error}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """CLI entry: ``churn-survival run <raw-file> [options]``."""
    raw_args = list(sys.argv[1:] if argv is None else argv)
    confirm_mapping = "--confirm-mapping" in raw_args
    persist_artifact = "--persist-artifact" in raw_args
    persist_run = "--persist-run" in raw_args
    args = [
        a
        for a in raw_args
        if a not in {"--confirm-mapping", "--persist-artifact", "--persist-run"}
    ]

    try:
        options, positional = _parse_flags(args)
    except ValueError as exc:
        print(f"ERROR: {exc}\nUsage: {_usage()}", file=sys.stderr)
        return 2
    if len(positional) != 1:
        print(f"Usage: {_usage()}", file=sys.stderr)
        return 2
    raw_path = positional[0]

    mapping_path = options["--mapping"]
    if confirm_mapping and not mapping_path:
        print("ERROR: --confirm-mapping requires --mapping <draft.json>", file=sys.stderr)
        return 2

    settings = get_settings()
    if options["--mock-dir"]:
        settings = settings.model_copy(
            update={"NODE3_MOCK_SOURCES_DIR": Path(str(options["--mock-dir"]))}
        )

    try:
        reference_date: date = settings.REFERENCE_DATE
        support_data = _load_support_data(options["--threads"])
        vocabulary = load_vocabulary()

        from config.loader import load_action_rules

        sources_config = None
        identity_mapping = None
        if options["--sources"]:
            from node3.node import _select_sources

            base_sources = load_node3_sources_config(str(options["--sources-config"]))
            sources_config = _select_sources(
                base_sources,
                str(options["--sources"]),
                options["--source-mode"],
                default_mode=settings.NODE3_SOURCE_MODE,
            )
            identity_version = options["--identity-map"] or base_sources.identity_mapping_version
            identity_mapping = load_identity_mapping(str(identity_version))

        mapping_gate = None
        mapping_report: MappingReport | None = None
        if confirm_mapping and mapping_path:
            mapping_report = MappingReport.model_validate(
                json.loads(Path(mapping_path).read_text(encoding="utf-8"))
            )
            mapping_gate = CallbackMappingGate(lambda report, _fp: report, name="cli")

        result = run_pipeline(
            raw_path,
            node1_version=str(options["--node1"]),
            node2_version=str(options["--node2"]),
            node3_version=str(options["--node3"]),
            node4_version=str(options["--node4"]),
            node5_version=str(options["--node5"]),
            support_data=support_data,
            sources_config=sources_config,
            identity_mapping=identity_mapping,
            action_rules=load_action_rules(str(options["--action-rules"])),
            vocabulary=vocabulary,
            settings=settings,
            mapping_gate=mapping_gate,
            mapping_report=mapping_report,
            mapping_node1_config_version=options["--node1-config"],
            persist_artifact=persist_artifact,
            reference_date=reference_date,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary must fail loudly
        print(f"ERROR: run failed: {exc}", file=sys.stderr)
        return 1

    _print_summary(result)

    if persist_run:
        from orchestration.persistence import RunStore

        if result.state.run_id:
            run_base = (
                Path(str(options["--run-dir"])) if options["--run-dir"] else settings.RUN_DIR
            )
            store = RunStore(run_base)
            target = store.save(result)
            print(f"Run: persisted -> {target}")
        else:
            print(
                "Run: warning: nothing persisted (no run_id computed)",
                file=sys.stderr,
            )

    if result.status == PipelineStatus.STOPPED_NEEDS_MAPPING:
        from node1.node import _print_onboarding_guide

        _print_onboarding_guide(result.state.fingerprint)

    output_path = options["--output"]
    if output_path:
        result.save(output_path)
        print(f"Run: result written -> {output_path}")

    return 0 if result.completed else 1


if __name__ == "__main__":
    sys.exit(main())

"""Node 1 entry point (architecture §1.1, ROADMAP Task 2.10).

Composes Node 1 as plain Python functions — no graph framework here; LangGraph
(Phase 7) calls this node. Flow: load raw -> fingerprint -> route -> transform
-> validate -> feature-gate -> report.

When no deterministic adapter matches, ``run_node1`` raises ``UnmappedFormatError``
carrying the fingerprint; the caller may invoke the LLM mapping-report workflow
(``run_mapping_workflow``) for a human-confirmed translation.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from config.loader import load_node1_config
from config.models import Node1Config
from config.settings import get_settings
from node1.feature_gate import feature_gate_records, feature_gate_warnings
from node1.report import build_report
from node1.validation import validate_records
from router.fingerprint import extract_fingerprint
from router.router import route

SUPPORTED_EXTENSIONS = {".csv", ".xlsx", ".xls"}


class UnmappedFormatError(RuntimeError):
    """Raised when no deterministic adapter matches the incoming shape (§0.1)."""

    def __init__(self, message: str, *, fingerprint: Any) -> None:
        super().__init__(message)
        self.fingerprint = fingerprint


def load_raw(path: str | Path) -> Any:
    """Load raw data: CSV -> single DataFrame; Excel -> dict[str, DataFrame]."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"raw data file not found: {path}")
    import pandas as pd

    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    if path.suffix.lower() in {".xlsx", ".xls"}:
        frames = pd.read_excel(path, sheet_name=None)
        return {str(name): frame for name, frame in frames.items()}
    raise ValueError(
        f"unsupported raw-data extension {path.suffix!r}; supported: {sorted(SUPPORTED_EXTENSIONS)}"
    )


def _build_adapter_list(config_dir: str | Path | None = None) -> list[Any]:
    from adapters.mapping_adapter import load_confirmed_mapping_adapters
    from router.router import _default_adapters

    adapters = list(_default_adapters())
    adapters.extend(load_confirmed_mapping_adapters(config_dir))
    return adapters


def _stamp_ingested_at(records: list[dict[str, Any]], now: datetime) -> None:
    stamp = now.astimezone(UTC).isoformat().replace("+00:00", "Z")
    for record in records:
        record.setdefault("meta", {})["ingested_at"] = stamp


def run_node1(
    path: str | Path,
    *,
    reference_date: date | None = None,
    now: datetime | None = None,
    config: Node1Config | None = None,
    adapters: list[Any] | None = None,
) -> Any:
    """Run the full Node 1 flow on a raw file. Returns a ``Node1Output``.

    Raises ``UnmappedFormatError`` when no deterministic adapter matches.
    """
    settings = get_settings()
    reference_date = reference_date or settings.REFERENCE_DATE
    config = config or load_node1_config("1")
    now = now or datetime.now(UTC)
    adapters = adapters if adapters is not None else _build_adapter_list()

    raw = load_raw(path)
    fingerprint = extract_fingerprint(raw)
    decision = route(
        fingerprint,
        adapters,
        high_confidence_threshold=config.router_high_confidence_threshold,
    )
    if not decision.matched or decision.adapter is None:
        raise UnmappedFormatError(decision.rationale, fingerprint=fingerprint)

    adapter = decision.adapter
    records = adapter.transform(raw, reference_date.isoformat())
    _stamp_ingested_at(records, now)

    # Feature gate runs BEFORE validation so Gate 8 only ever sees approved core
    # keys: unapproved keys are demoted to extra_features (with per-key counts
    # surfaced in the report) instead of rejecting the record (§1.8). Gate 8's
    # strict CORE_KEYS/CORE_TYPE checks remain as defense-in-depth for callers
    # that invoke validate_records directly.
    records, demoted_features = feature_gate_records(records, config.approved_core_keys)
    validation = validate_records(records, config=config, reference_date=reference_date)
    warnings = feature_gate_warnings(validation.accepted, config)

    mapping_version = adapter.get_mapping_config().get(
        "mapping_version", f"{adapter.name}_v{adapter.version}"
    )
    return build_report(
        records,
        validation,
        adapter_name=adapter.name,
        mapping_version=mapping_version,
        reference_date=reference_date,
        warnings=warnings,
        matched_candidates=list(decision.matched_candidates),
        demoted_features=demoted_features,
        missingness_passthrough=validation.missingness_passthrough,
    )


def run_mapping_workflow(
    path: str | Path,
    *,
    client: Any = None,
    reference_date: date | None = None,
) -> Any:
    """LLM mapping-report path for an unmapped shape (§1.6). Returns a MappingReport.

    The report still requires human confirmation before it becomes configuration.
    """
    raw = load_raw(path)
    fingerprint = extract_fingerprint(raw)
    from router.llm_mapper import generate_mapping_report

    return generate_mapping_report(fingerprint, raw, client=client)


def build_draft_mapping_report(path: str | Path) -> Any:
    """Draft a MappingReport skeleton for manual completion (§1.6 onboarding).

    Pre-fills the real fingerprint and lists every column as unmapped; the user
    fills in ``proposed_mappings`` / ``suggested_extra_features`` by hand (no LLM
    required). Deterministic: same file -> same draft shape.
    """
    from datetime import UTC

    raw = load_raw(path)
    fingerprint = extract_fingerprint(raw)
    from schemas.mapping import MappingReport

    return MappingReport(
        source_fingerprint=fingerprint,
        proposed_mappings=[],
        unmapped_columns=list(fingerprint.column_names),
        suggested_extra_features=[],
        data_quality_flags=[],
        recommended_action="create_deterministic_adapter",
        llm_model_used="manual/template",
        generated_at=datetime.now(UTC),
    )


def map_main(argv: list[str] | None = None) -> int:
    """CLI entry: ``churn-survival map <raw-file> [--llm] [--out <file>] [--confirm <draft.json>]``.

    Produces a draft MappingReport the user fills in and confirms. With ``--llm``
    a proposal is generated (requires a configured LLM); with ``--confirm`` an
    edited draft is validated and persisted as a deterministic adapter. Pass
    ``--node1-config <version>`` alongside ``--confirm`` to record the deployment
    Node 1 config so full-pipeline runs auto-resolve it.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(
            "Usage: churn-survival map <raw-file> [--llm] [--out <draft.json>] "
            "| churn-survival map <draft.json> --confirm [--node1-config <version>]",
            file=sys.stderr,
        )
        return 2
    if args[0] == "--confirm":
        print(
            "Usage: churn-survival map <draft.json> --confirm "
            "[--node1-config <version>]  (persists a filled-in draft)",
            file=sys.stderr,
        )
        return 2
    if len(args) >= 2 and args[-1] == "--confirm":
        node1_config_version = None
        if "--node1-config" in args:
            flag_index = args.index("--node1-config")
            if flag_index + 1 >= len(args):
                print(
                    "Usage: churn-survival map <draft.json> --confirm "
                    "[--node1-config <version>]",
                    file=sys.stderr,
                )
                return 2
            node1_config_version = args[flag_index + 1]
            args = args[:flag_index] + args[flag_index + 2 :]
        draft_path = Path(args[0])
        try:
            from config.loader import config_dir as resolve_config_dir
            from config.loader import load_config
            from router.llm_mapper import confirm_and_persist
            from schemas.mapping import MappingReport

            report = load_config(draft_path, MappingReport)
            config = confirm_and_persist(
                report,
                config_dir=resolve_config_dir(),
                confirmed_by="cli",
                node1_config_version=node1_config_version,
            )
        except Exception as exc:  # noqa: BLE001 - CLI boundary must fail loudly
            print(f"ERROR: mapping confirmation failed: {exc}", file=sys.stderr)
            return 1
        mappings_dir = resolve_config_dir() / "mappings"
        print(f"Confirmed {config.mapping_version} -> {mappings_dir / config.mapping_version}.json")
        if config.node1_config_version:
            print(
                "Deployment Node 1 config recorded: "
                f"config/node1/v{config.node1_config_version}.json"
            )
        else:
            print("Now create config/node1/v<company>.json and re-run node1 --config <company>.")
        return 0

    use_llm = "--llm" in args
    out_arg = None
    if "--out" in args:
        flag_index = args.index("--out")
        if flag_index + 1 >= len(args):
            print("Usage: ... --out <draft.json>", file=sys.stderr)
            return 2
        out_arg = args[flag_index + 1]
        args = args[:flag_index] + args[flag_index + 2 :]
    args = [a for a in args if a != "--llm"]
    if len(args) != 1:
        print(
            "Usage: churn-survival map <raw-file> [--llm] [--out <draft.json>]",
            file=sys.stderr,
        )
        return 2
    try:
        report = run_mapping_workflow(args[0]) if use_llm else build_draft_mapping_report(args[0])
    except Exception as exc:  # noqa: BLE001 - CLI boundary must fail loudly
        print(f"ERROR: mapping draft failed: {exc}", file=sys.stderr)
        return 1

    if out_arg:
        out_path = Path(out_arg)
    else:
        out_path = (
            Path("config")
            / "mappings"
            / "drafts"
            / f"draft_{report.source_fingerprint.headers_hash[:12]}.json"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Draft mapping report written to {out_path}")
    print("Fill in proposed_mappings / suggested_extra_features, then run:")
    print(f"  churn-survival map {out_path} --confirm")
    return 0


def _print_onboarding_guide(fingerprint: Any) -> None:
    """Tell the user exactly how to register a deterministic adapter (§1.5/§1.6)."""
    import sys

    print("", file=sys.stderr)
    print("The file shape is unknown. Onboarding a new dataset/company is:", file=sys.stderr)
    print("  1. Generate a draft mapping report:", file=sys.stderr)
    print(
        "       churn-survival map <raw-file>   # fill in the mappings, then confirm",
        file=sys.stderr,
    )
    print("     (or review the LLM proposal if LLM_PROVIDER is configured).", file=sys.stderr)
    print(
        "  2. The confirmed report becomes a deterministic adapter in config/mappings/:",
        file=sys.stderr,
    )
    print("       config/mappings/map_<ts>.json", file=sys.stderr)
    print("  3. Create a deployment config from the template:", file=sys.stderr)
    print("       config/node1/_template.json  ->  config/node1/v<company>.json", file=sys.stderr)
    print("       (approved_core_keys + core_key_types for your columns)", file=sys.stderr)
    print("     Record it on the mapping so full-pipeline runs auto-resolve it:", file=sys.stderr)
    print(
        "       churn-survival map <draft.json> --confirm --node1-config <company>",
        file=sys.stderr,
    )
    print(
        "  4. If you approve a brand-new core feature, add it to CoreFeatures",
        file=sys.stderr,
    )
    print("       (schemas/canonical.py).", file=sys.stderr)
    print(
        "  5. If a new date/tenure derivation is needed, extend the audited ops",
        file=sys.stderr,
    )
    print("       (adapters/mapping_adapter.py).", file=sys.stderr)
    print("  6. Re-run:", file=sys.stderr)
    print("       churn-survival node1 <raw-file> --config <company>", file=sys.stderr)
    print("  See docs/onboarding.md for the full walkthrough.", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    """CLI entry: ``churn-survival node1 <raw-file> [--config <version>]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    config_version = "1"
    if "--config" in args:
        flag_index = args.index("--config")
        if flag_index + 1 >= len(args):
            print(
                "Usage: churn-survival node1 <raw-file> [--config <version>]",
                file=sys.stderr,
            )
            return 2
        config_version = args[flag_index + 1]
        args = args[:flag_index] + args[flag_index + 2 :]
    if len(args) != 1:
        print(
            "Usage: churn-survival node1 <raw-file> [--config <version>]",
            file=sys.stderr,
        )
        return 2
    try:
        config = load_node1_config(config_version)
        output = run_node1(args[0], config=config)
    except UnmappedFormatError as exc:
        print(
            "ERROR: no deterministic adapter matched; routing to LLM mapping-report path "
            f"(fingerprint headers_hash={exc.fingerprint.headers_hash}).",
            file=sys.stderr,
        )
        _print_onboarding_guide(exc.fingerprint)
        return 1
    except Exception as exc:  # noqa: BLE001 - CLI boundary must fail loudly
        print(f"ERROR: Node 1 failed: {exc}", file=sys.stderr)
        return 1

    report = output.validation_report
    matched = ",".join(report.matched_candidates) or "-"
    print(
        f"Node 1: status={report.status.value} accepted={report.n_accepted} "
        f"rejected={report.n_rejected} adapter={report.adapter_used} "
        f"matched={matched}"
    )
    if report.demoted_features:
        demoted = ",".join(f"{key}={count}" for key, count in report.demoted_features.items())
        print(f"Node 1: core keys demoted to extra_features: {demoted}")
    if report.missingness_passthrough:
        passed = ",".join(
            f"{key}={count}" for key, count in report.missingness_passthrough.items()
        )
        print(f"Node 1: core keys missing within threshold, passed through as null: {passed}")
    from logging_setup import emit_node_completion

    emit_node_completion(
        "node1",
        config_version=config_version,
        adapter=report.adapter_used,
        validation_status=report.status.value,
        n_accepted=report.n_accepted,
        n_rejected=report.n_rejected,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

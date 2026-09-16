"""Node 5 entry point — client-facing risk report (architecture §5.26/§5.27).

Node 5 is a presentation layer over the frozen `Node4Output`. It never
recalculates risk, changes a level/rank/score/confidence, or invents evidence.
The LLM is optional explanation polish only; the deterministic template path is
complete on its own (D-MILESTONES A).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

from config.loader import load_action_rules, load_node5_config
from config.models import ActionRulesConfig, Node5Config
from node5.report.consistency import enforce_consistency
from node5.report.deterministic_sections import (
    build_data_quality_notes,
    build_executive_summary,
    build_methodology,
    build_risk_distribution,
)
from node5.report.evidence import build_evidence, build_node3_index
from node5.report.recommendations import select_recommendation
from node5.report.transformer import (
    CustomerProfile,
    build_customer_report,
    coerce_customer_data,
    display_name_for,
)
from node5.validation.node4_validator import (
    Node5ValidationError,
    validate_node4_output,
)
from router.llm_mapper import LlmClient
from schemas.node3 import Node3Output
from schemas.node4 import Node4Output, RankedAccount
from schemas.node5 import (
    DataQualitySection,
    Node5Output,
    Node5ProcessingReport,
    ReportContent,
    ReportMetadata,
)

TITLE = "Customer Risk Report"
WARNING_LANGUAGE_UNSUPPORTED = (
    "language is not 'en'; Node 5 v1 emits English templates only."
)


def _generated_at(reference_date) -> datetime:
    """D-U3: deterministic, derived from the declared reference date."""
    return datetime.combine(reference_date, time(0, 0), tzinfo=UTC)


def _resolve_version(values: Sequence[str], field: str, errors: list[dict[str, Any]]) -> str:
    """D-U2: unique non-empty version, else first non-empty + structured error."""
    non_empty = [value for value in values if value]
    if not non_empty:
        return ""
    unique = list(dict.fromkeys(non_empty))
    if len(unique) == 1:
        return unique[0]
    errors.append(
        {
            "code": "MIXED_PROVENANCE",
            "field": field,
            "message": (
                f"{field} has {len(unique)} distinct non-empty values; using the first "
                "in Node 4 order (deterministic, not fabricated)"
            ),
        }
    )
    return non_empty[0]


def _versions(node4_output: Node4Output, errors: list[dict[str, Any]]) -> dict[str, str]:
    accounts = [*node4_output.ranked_accounts, *node4_output.insufficient_data_accounts]
    return {
        "node2_model_version": _resolve_version(
            [a.evidence_refs.node2.model_version for a in accounts],
            "node2_model_version",
            errors,
        ),
        "node3_signal_version": _resolve_version(
            [a.evidence_refs.node3.signal_version for a in accounts],
            "node3_signal_version",
            errors,
        ),
        "node4_ranking_version": _resolve_version(
            [a.meta.ranking_version for a in accounts], "node4_ranking_version", errors
        ),
        "node4_threshold_version": _resolve_version(
            [a.meta.threshold_version for a in accounts], "node4_threshold_version", errors
        ),
        "node4_critical_rules_version": _resolve_version(
            [a.meta.critical_rules_version for a in accounts],
            "node4_critical_rules_version",
            errors,
        ),
    }


def _explain(
    account: RankedAccount,
    display_name: str,
    config: Node5Config,
    client: LlmClient | None,
    counters: dict[str, int],
    warnings: list[str],
    *,
    recommended_action: str | None,
) -> tuple[str | None, str | None]:
    """Optional LLM explanation with deterministic validation + fallback (D-LLM)."""
    if client is None:
        return None, None
    from node5.llm.explainer import explain_account

    return explain_account(
        account,
        display_name,
        config,
        client,
        counters,
        warnings,
        recommended_action=recommended_action,
    )


def run_node5(
    node4_output: Node4Output,
    config: Node5Config,
    *,
    customer_data: Mapping[str, CustomerProfile] | None = None,
    node3_output: Node3Output | None = None,
    action_rules: ActionRulesConfig | None = None,
    llm_client: LlmClient | None = None,
) -> Node5Output:
    """Full Node 5 run (architecture §5.27). Mandatory validation fails loudly."""
    validation = validate_node4_output(
        node4_output, expected_reference_date=config.reference_date
    )
    if not validation.ok:
        raise Node5ValidationError(validation.errors)

    context = coerce_customer_data(customer_data)
    node3_index = build_node3_index(node3_output)

    errors: list[dict[str, Any]] = []
    warnings: list[str] = []
    versions = _versions(node4_output, errors)
    if config.language != "en":
        warnings.append(WARNING_LANGUAGE_UNSUPPORTED)
    if config.include_recommendations and action_rules is None:
        warnings.append(
            "recommendations are enabled but no ACTION_RULES were provided; no "
            "recommendations will be emitted."
        )

    priority_source = list(node4_output.ranked_accounts)
    capped = priority_source[: config.max_accounts_in_summary]
    if len(capped) < len(priority_source):
        warnings.append(
            f"priority_accounts truncated to max_accounts_in_summary="
            f"{config.max_accounts_in_summary} of {len(priority_source)} ranked accounts."
        )
    insufficient_source = (
        list(node4_output.insufficient_data_accounts)
        if config.include_insufficient_data
        else []
    )

    counters = {"llm_calls": 0, "llm_failures": 0}
    priority_reports = []
    for account in capped:
        display_name = display_name_for(account.customer_id, context)
        action, _ = select_recommendation(
            account, action_rules, enabled=config.include_recommendations
        )
        evidence = build_evidence(
            account,
            node3_index,
            include=config.include_evidence,
            mode=config.evidence_mode,
            max_items=config.max_evidence_per_account,
            warnings=warnings,
            errors=errors,
        )
        headline, summary = _explain(
            account,
            display_name,
            config,
            llm_client,
            counters,
            warnings,
            recommended_action=action,
        )
        priority_reports.append(
            build_customer_report(
                account,
                display_name=display_name,
                recommended_action=action,
                evidence=evidence,
                errors=errors,
                headline=headline,
                summary=summary,
            )
        )

    insufficient_reports = []
    for account in insufficient_source:
        display_name = display_name_for(account.customer_id, context)
        evidence = build_evidence(
            account,
            node3_index,
            include=config.include_evidence,
            mode=config.evidence_mode,
            max_items=config.max_evidence_per_account,
            warnings=warnings,
            errors=errors,
        )
        insufficient_reports.append(
            build_customer_report(
                account,
                display_name=display_name,
                recommended_action=None,
                evidence=evidence,
                errors=errors,
            )
        )

    report = ReportContent(
        title=TITLE,
        reference_date=node4_output.reference_date,
        executive_summary=build_executive_summary(node4_output, config),
        risk_distribution=build_risk_distribution(node4_output),
        priority_accounts=priority_reports,
        insufficient_data_accounts=insufficient_reports,
        data_quality=DataQualitySection(
            notes=build_data_quality_notes(node4_output, config)
        ),
        methodology=build_methodology(),
    )
    metadata = ReportMetadata(
        report_version=config.report_version,
        node2_model_version=versions["node2_model_version"],
        node3_signal_version=versions["node3_signal_version"],
        node4_ranking_version=versions["node4_ranking_version"],
        node4_threshold_version=versions["node4_threshold_version"],
        node4_critical_rules_version=versions["node4_critical_rules_version"],
        prompt_version=config.prompt_version,
        llm_model_version=llm_client.model if llm_client is not None else None,
        reference_date=node4_output.reference_date,
        generated_at=_generated_at(node4_output.reference_date),
        action_rules_version=(
            action_rules.action_rules_version if action_rules is not None else None
        ),
    )
    processing = Node5ProcessingReport(
        n_accounts=len(node4_output.ranked_accounts)
        + len(node4_output.insufficient_data_accounts),
        n_accounts_reported=len(priority_reports) + len(insufficient_reports),
        n_insufficient_data=node4_output.summary_stats.n_insufficient_data,
        llm_calls=counters["llm_calls"],
        llm_failures=counters["llm_failures"],
        validation_errors=len(errors),
        warnings=warnings,
        errors=errors,
    )
    output = Node5Output(report=report, metadata=metadata, processing_report=processing)
    enforce_consistency(
        output,
        node4_output,
        require_action_rules=config.include_recommendations,
    )
    return output


def _usage() -> str:
    return (
        "churn-survival node5 --node4 <node4_output.json> [--node3 <node3_output.json>] "
        "[--customer-data <context.json>] [--config <node5_version>] "
        "[--action-rules <version>] [--output <out.json>] [--html <out.html>]"
    )


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    """CLI for ``churn-survival node5`` (reads saved Node 4 / Node 3 outputs)."""
    args = list(sys.argv[1:] if argv is None else argv)
    node4_path: str | None = None
    node3_path: str | None = None
    customer_path: str | None = None
    output_path: str | None = None
    html_path: str | None = None
    config_version = "1"
    action_rules_version = "1"

    for flag in (
        "--node4",
        "--node3",
        "--customer-data",
        "--config",
        "--action-rules",
        "--output",
        "--html",
    ):
        if flag in args:
            index = args.index(flag)
            if index + 1 >= len(args):
                print(f"Usage: {_usage()}", file=sys.stderr)
                return 2
            value = args[index + 1]
            args = args[:index] + args[index + 2 :]
            if flag == "--node4":
                node4_path = value
            elif flag == "--node3":
                node3_path = value
            elif flag == "--customer-data":
                customer_path = value
            elif flag == "--config":
                config_version = value
            elif flag == "--action-rules":
                action_rules_version = value
            elif flag == "--output":
                output_path = value
            else:
                html_path = value

    if args or node4_path is None:
        print(f"Usage: {_usage()}", file=sys.stderr)
        return 2

    try:
        config = load_node5_config(config_version)
        node4_output = Node4Output.model_validate(_load_json(node4_path))
        node3_output = (
            Node3Output.model_validate(_load_json(node3_path))
            if node3_path is not None
            else None
        )
        customer_data = (
            coerce_customer_data(_load_json(customer_path))
            if customer_path is not None
            else None
        )
        action_rules = load_action_rules(action_rules_version)
        output = run_node5(
            node4_output,
            config,
            customer_data=customer_data,
            node3_output=node3_output,
            action_rules=action_rules,
        )
    except Exception as exc:  # noqa: BLE001 - CLI boundary must fail loudly
        print(f"ERROR: Node 5 failed: {exc}", file=sys.stderr)
        return 1

    report = output.report
    stats = report.risk_distribution
    print(
        f"Node 5: accounts={output.processing_report.n_accounts} "
        f"reported={output.processing_report.n_accounts_reported} "
        f"critical={stats.critical} high={stats.high} medium={stats.medium} "
        f"low={stats.low} insufficient={stats.insufficient_data}"
    )
    print(
        f"Node 5: llm_calls={output.processing_report.llm_calls} "
        f"llm_failures={output.processing_report.llm_failures}"
    )
    if output_path is not None:
        from node5.rendering.json import render_json

        Path(output_path).write_text(render_json(output) + "\n", encoding="utf-8")
        print(f"Node 5: output written -> {output_path}")
    if html_path is not None:
        from node5.rendering.html import render_html

        Path(html_path).write_text(render_html(output), encoding="utf-8")
        print(f"Node 5: HTML written -> {html_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Node 3 entry point + CLI (architecture §3.1/§3.11, ROADMAP Tasks 4.1/4.11/4.12).

Composes deterministic preprocessing, thread-level extraction, and customer-level
aggregation into the §3.11 ``Node3Output``. Plain Python only — LangGraph (Phase 7)
calls this node; statistical/LLM work stays in its modules.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from config.loader import (
    load_identity_mapping,
    load_node3_config,
    load_node3_sources_config,
    load_vocabulary,
)
from config.models import (
    IdentityMappingConfig,
    Node3Config,
    Node3SourcesConfig,
    VocabularyConfig,
)
from config.settings import Settings, get_settings
from node3.aggregate import aggregate_customer
from node3.clock import run_timestamp
from node3.llm_extractor import extract_thread_signals
from node3.preprocess import preprocess_threads
from node3.sources.collision import resolve_support_external_id_collisions
from node3.sources.errors import SourceDataError, SourceError, redact_secrets
from node3.sources.identity import resolve_identities
from node3.sources.normalize import normalize_threads
from node3.sources.registry import (
    agent_identities_by_source,
    build_sources_safe,
)
from node3.vocabulary import check_vocabulary_governance
from router.llm_mapper import LlmClient
from schemas.external import ExternalMessage
from schemas.node3 import (
    CustomerSupportSignals,
    Node3Output,
    Node3ProcessingReport,
    SupportThread,
    ThreadSignals,
)


def _create_llm_client_or_none() -> LlmClient | None:
    """Build an LLM client only when a provider is configured (§3.9)."""
    if get_settings().LLM_PROVIDER == "none":
        return None
    from router.llm_mapper import create_llm_client

    return create_llm_client()


def run_node3(
    customers: Sequence[str],
    support_data: Sequence[SupportThread | dict[str, object]] | None,
    config: Node3Config,
    *,
    external_threads: Sequence[SupportThread | dict[str, object]] | None = None,
    extra_warnings: Sequence[str] = (),
    extra_errors: Sequence[dict[str, object]] = (),
    llm_client: LlmClient | None = None,
    vocabulary: VocabularyConfig | None = None,
    now: datetime | None = None,
) -> Node3Output:
    """Full Node 3 run: preprocess -> extract -> aggregate -> §3.11 output.

    ``external_threads`` are source-neutral threads produced by the multi-source
    ingestion layer; they are merged with ``support_data`` before preprocessing so
    the extraction/aggregation logic is identical regardless of origin.

    Customers with no support data get an honest ``no_data`` record (confidence
    0.0, strength ``none``, summary ``None``) — never a fabricated signal.
    """
    now = run_timestamp(config, now)
    vocab = vocabulary or load_vocabulary()

    requested = list(dict.fromkeys(customers))
    support_list = list(support_data or [])
    external_list = list(external_threads or [])
    collision_errors: list[dict[str, object]] = []
    if external_list and all(isinstance(t, SupportThread) for t in external_list):
        collision = resolve_support_external_id_collisions(support_list, external_list)  # type: ignore[arg-type]
        external_list = list(collision.external_threads)
        collision_errors = collision.errors
    combined = support_list + external_list
    items, stats = preprocess_threads(combined, config)

    outcomes = []
    llm_calls = 0
    for item in items:
        outcome = extract_thread_signals(
            item, config, client=llm_client, vocabulary=vocab, now=now
        )
        outcomes.append(outcome)
        if outcome.llm_called:
            llm_calls += 1

    threads_by_customer: dict[str, list[ThreadSignals]] = defaultdict(list)
    failed_by_customer: dict[str, set[str]] = defaultdict(set)
    for outcome in outcomes:
        signals = outcome.signals
        threads_by_customer[signals.customer_id].append(signals)
        if outcome.failed:
            failed_by_customer[signals.customer_id].add(signals.thread_id)

    customer_signals: list[CustomerSupportSignals] = []
    for customer_id in requested:
        customer_signals.append(
            aggregate_customer(
                customer_id,
                threads_by_customer.get(customer_id, []),
                config,
                failed_thread_ids=failed_by_customer.get(customer_id, set()),
                vocabulary=vocab,
                now=now,
            )
        )

    failed_threads = sum(1 for outcome in outcomes if outcome.failed)
    n_with_data = sum(1 for signal in customer_signals if signal.has_support_data)
    n_with_signals = sum(
        1
        for signal in customer_signals
        if signal.risk_flags or signal.churn_language_detected
    )

    warnings: list[str] = [*extra_warnings]
    if stats.n_threads_over_limit:
        warnings.append(
            f"{stats.n_threads_over_limit} thread(s) dropped over "
            f"max_threads_per_customer={config.max_threads_per_customer}"
        )
    if stats.n_threads_over_token_budget:
        warnings.append(
            f"{stats.n_threads_over_token_budget} thread(s) dropped over "
            f"max_tokens_per_customer={config.max_tokens_per_customer}"
        )
    if stats.n_unknown_language:
        warnings.append(f"{stats.n_unknown_language} thread(s) with unknown language")
    if stats.n_unsupported_language:
        warnings.append(
            f"{stats.n_unsupported_language} thread(s) in unsupported language — quarantined"
        )
    if stats.n_dropped_invalid:
        warnings.append(f"{stats.n_dropped_invalid} invalid thread(s) dropped")
    if stats.n_out_of_window:
        warnings.append(f"{stats.n_out_of_window} thread(s) outside the lookback window")
    if collision_errors:
        warnings.append(
            f"{len(collision_errors)} external thread(s) dropped due to identifier "
            "collision with existing data"
        )
    all_flags = [flag for signals in customer_signals for flag in signals.risk_flags]
    warnings.extend(check_vocabulary_governance(all_flags, vocab))

    errors: list[dict[str, object]] = [*extra_errors, *collision_errors, *stats.errors]
    errors.extend(outcome.error for outcome in outcomes if outcome.error is not None)

    return Node3Output(
        customer_signals=customer_signals,
        thread_signals=[outcome.signals for outcome in outcomes],
        processing_report=Node3ProcessingReport(
            n_customers_requested=len(requested),
            n_customers_with_data=n_with_data,
            n_customers_with_signals=n_with_signals,
            n_threads_processed=len(items) - failed_threads,
            n_threads_failed=failed_threads,
            n_cross_channel_duplicates_collapsed=stats.n_duplicates_collapsed,
            llm_calls=llm_calls,
            warnings=warnings,
            errors=errors,
        ),
    )


@dataclass
class SourceIngestionResult:
    """Outcome of fetching + resolving + normalizing every enabled source."""

    threads: list[SupportThread] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[dict[str, object]] = field(default_factory=list)
    n_messages_fetched: int = 0
    n_messages_unmapped: int = 0
    n_sources_used: int = 0


def ingest_external_sources(
    sources_config: Node3SourcesConfig,
    identity_mapping: IdentityMappingConfig,
    *,
    settings: Settings | None = None,
    customer_ids: Sequence[str] | None = None,
) -> SourceIngestionResult:
    """Fetch every enabled source, resolve identities, normalize to threads.

    Sources are isolated at both construction and fetch time: a build failure
    (missing directory, live credentials absent, unknown source) or a fetch
    failure (auth/API/not-implemented/malformed payload) is recorded as a
    structured error and the remaining sources continue. Messages whose external
    identity is not explicitly mapped are never attached to a customer.
    """
    result = SourceIngestionResult()
    resolved_settings = settings or get_settings()
    secrets = (
        resolved_settings.X_CLIENT_SECRET,
        resolved_settings.X_ACCESS_TOKEN,
        resolved_settings.GMAIL_CLIENT_SECRET,
        resolved_settings.GMAIL_REFRESH_TOKEN,
        resolved_settings.LLM_API_KEY,
    )
    build_result = build_sources_safe(sources_config, settings=resolved_settings)
    result.errors.extend(build_result.errors)
    result.warnings.extend(build_result.warnings)
    agent_identities = agent_identities_by_source(sources_config)

    fetched: list[ExternalMessage] = []
    for source in build_result.sources:
        try:
            messages = source.fetch_customer_data(customer_ids)
        except SourceDataError as exc:
            detail = redact_secrets(str(exc), secrets)
            result.errors.append(
                {
                    "source": source.name,
                    "code": "SOURCE_DATA_INVALID",
                    "detail": detail,
                }
            )
            result.warnings.append(f"source {source.name!r} returned invalid data: {detail}")
            continue
        except SourceError as exc:
            detail = redact_secrets(str(exc), secrets)
            result.errors.append(
                {
                    "source": source.name,
                    "code": "SOURCE_FETCH_FAILED",
                    "detail": detail,
                }
            )
            result.warnings.append(f"source {source.name!r} failed: {detail}")
            continue
        result.n_sources_used += 1
        fetched.extend(messages)

    resolution = resolve_identities(
        fetched, identity_mapping, agent_identities=agent_identities
    )
    result.errors.extend(resolution.errors)
    result.n_messages_fetched = len(fetched)
    result.n_messages_unmapped = len(resolution.unresolved)
    if resolution.unresolved:
        result.warnings.append(
            f"{len(resolution.unresolved)} external message(s) could not be mapped to a "
            "customer and were not used for analysis"
        )

    normalization = normalize_threads(resolution.messages)
    result.errors.extend(normalization.errors)
    if normalization.errors:
        result.warnings.append(
            f"{len(normalization.errors)} external thread(s) dropped during normalization"
        )
    result.threads = normalization.threads
    return result


def run_node3_from_sources(
    customers: Sequence[str],
    config: Node3Config,
    sources_config: Node3SourcesConfig,
    identity_mapping: IdentityMappingConfig,
    *,
    support_data: Sequence[SupportThread | dict[str, object]] | None = None,
    settings: Settings | None = None,
    llm_client: LlmClient | None = None,
    vocabulary: VocabularyConfig | None = None,
    now: datetime | None = None,
) -> Node3Output:
    """Run Node 3 over one or more external sources (+ optional support data)."""
    ingestion = ingest_external_sources(
        sources_config, identity_mapping, settings=settings, customer_ids=customers
    )
    return run_node3(
        customers,
        support_data,
        config,
        external_threads=ingestion.threads,
        extra_warnings=ingestion.warnings,
        extra_errors=ingestion.errors,
        llm_client=llm_client,
        vocabulary=vocabulary,
        now=now,
    )


def _usage() -> str:
    return (
        "churn-survival node3 [<threads.json>] [--customers <ids.csv|ids.json>] "
        "[--config <node3_version>] [--sources <x,gmail|mock>] [--source-mode mock|live] "
        "[--sources-config <version>] [--identity-map <version>] [--output <out.json>]"
    )


def _load_customer_universe(path: str | Path) -> list[str]:
    """Read a customer universe: first CSV column, or a JSON list of ids."""
    file = Path(path)
    if file.suffix.lower() == ".json":
        raw = json.loads(file.read_text(encoding="utf-8"))
        return [
            str(item["customer_id"] if isinstance(item, dict) else item).strip()
            for item in raw
        ]
    import pandas as pd  # type: ignore[import-untyped]

    frame = pd.read_csv(file)
    if frame.empty:
        return []
    column = frame.columns[0]
    return [str(value).strip() for value in frame[column].dropna().tolist()]


def _default_customer_universe(raw: list[object]) -> list[str]:
    """Best-effort universe from raw thread entries (invalid entries are ignored).

    The CLI deliberately does *not* pre-validate every entry — ``run_node3`` /
    ``preprocess_threads`` drop malformed threads with structured errors — so this
    only reads ``customer_id`` when present and well-formed.
    """
    universe: list[str] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        customer_id = entry.get("customer_id")
        if isinstance(customer_id, str) and customer_id.strip() and customer_id not in universe:
            universe.append(customer_id)
    return universe


_FLAG_DEFAULTS: dict[str, str | None] = {
    "--config": "1",
    "--output": None,
    "--customers": None,
    "--sources": None,
    "--source-mode": None,
    "--sources-config": "1",
    "--identity-map": None,
    "--mock-dir": None,
}


def _parse_flags(args: list[str]) -> tuple[dict[str, str | None], list[str]]:
    """Split ``--flag value`` options from positional arguments."""
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


def _select_sources(
    base: Node3SourcesConfig,
    spec: str,
    mode: str | None,
    *,
    default_mode: str = "mock",
) -> Node3SourcesConfig:
    """Filter/override the configured sources from a CLI ``--sources`` spec.

    Precedence (QA F-6/F-12):
    1. ``enabled`` is a hard gate: an explicitly requested disabled source is a
       configuration error — it is never silently reinterpreted as "no source".
    2. ``--source-mode`` (explicit CLI intent) overrides the per-source ``mode``
       for the selected sources.
    3. A per-source ``mode`` overrides the global ``NODE3_SOURCE_MODE`` default.
    4. The ``mock`` shorthand selects only *enabled* sources whose effective mode
       is mock (per-source mode, else the global default); combining it with
       ``--source-mode live`` is contradictory and rejected. Mode is never
       silently switched in either direction.
    """
    tokens = [token.strip() for token in spec.split(",") if token.strip()]
    if not tokens:
        raise ValueError("--sources must name at least one source")

    if tokens == ["mock"]:
        if mode == "live":
            raise ValueError("--sources mock cannot be combined with --source-mode live")
        selected = {
            name: source
            for name, source in base.sources.items()
            if source.enabled and (source.mode or default_mode) == "mock"
        }
    else:
        selected = {}
        for name in tokens:
            if name not in base.sources:
                raise KeyError(f"unknown source {name!r}; configured: {sorted(base.sources)}")
            source = base.sources[name]
            if not source.enabled:
                raise ValueError(
                    f"source {name!r} is disabled in the sources configuration"
                )
            selected[name] = source
        if mode:
            selected = {
                name: source.model_copy(update={"mode": mode})
                for name, source in selected.items()
            }

    if not selected:
        raise ValueError("--sources selected no enabled sources")
    return base.model_copy(update={"sources": selected})


def _customers_from_mapping(identity: IdentityMappingConfig) -> list[str]:
    """Deterministic customer universe from the identity mapping."""
    customers: list[str] = []
    for pairs in identity.mappings.values():
        for customer_id in pairs.values():
            if customer_id not in customers:
                customers.append(customer_id)
    return customers


def main(argv: list[str] | None = None) -> int:
    """CLI for ``churn-survival node3``."""
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        options, positional = _parse_flags(args)
    except ValueError as exc:
        print(f"ERROR: {exc}\nUsage: {_usage()}", file=sys.stderr)
        return 2
    if len(positional) > 1:
        print(f"Usage: {_usage()}", file=sys.stderr)
        return 2

    threads_path = positional[0] if positional else None
    sources_spec = options["--sources"]
    if threads_path is None and sources_spec is None:
        print(f"Usage: {_usage()}", file=sys.stderr)
        return 2

    settings = get_settings()
    if options["--mock-dir"]:
        settings = settings.model_copy(
            update={"NODE3_MOCK_SOURCES_DIR": Path(str(options["--mock-dir"]))}
        )

    try:
        config = load_node3_config(str(options["--config"]))
        vocabulary = load_vocabulary()
        raw: list[Any] | None = None
        if threads_path is not None:
            loaded = json.loads(Path(threads_path).read_text(encoding="utf-8"))
            if not isinstance(loaded, list):
                raise ValueError("threads file must contain a JSON list of thread objects")
            raw = loaded

        if sources_spec is not None:
            base_sources = load_node3_sources_config(str(options["--sources-config"]))
            sources_config = _select_sources(
                base_sources,
                sources_spec,
                options["--source-mode"],
                default_mode=settings.NODE3_SOURCE_MODE,
            )
            identity_version = options["--identity-map"] or base_sources.identity_mapping_version
            identity = load_identity_mapping(str(identity_version))
            if options["--customers"]:
                customers = _load_customer_universe(str(options["--customers"]))
            else:
                customers = list(
                    dict.fromkeys(
                        [
                            *_default_customer_universe(raw or []),
                            *_customers_from_mapping(identity),
                        ]
                    )
                )
            output = run_node3_from_sources(
                customers,
                config,
                sources_config,
                identity,
                support_data=raw,
                settings=settings,
                llm_client=_create_llm_client_or_none(),
                vocabulary=vocabulary,
            )
        else:
            customers = (
                _load_customer_universe(str(options["--customers"]))
                if options["--customers"]
                else _default_customer_universe(raw or [])
            )
            output = run_node3(
                customers,
                raw,
                config,
                llm_client=_create_llm_client_or_none(),
                vocabulary=vocabulary,
            )
    except Exception as exc:  # noqa: BLE001 - CLI boundary must fail loudly
        print(f"ERROR: Node 3 failed: {exc}", file=sys.stderr)
        return 1

    report = output.processing_report
    print(
        f"Node 3: customers={report.n_customers_requested} "
        f"with_data={report.n_customers_with_data} "
        f"with_signals={report.n_customers_with_signals}"
    )
    print(
        f"Node 3: threads_processed={report.n_threads_processed} "
        f"failed={report.n_threads_failed} "
        f"duplicates_collapsed={report.n_cross_channel_duplicates_collapsed} "
        f"llm_calls={report.llm_calls}"
    )
    output_path = options["--output"]
    if output_path:
        Path(output_path).write_text(
            json.dumps(output.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
        )
        print(f"Node 3: output written -> {output_path}")
    from logging_setup import emit_node_completion

    emit_node_completion(
        "node3",
        config_version=str(options["--config"]),
        n_customers_with_signals=report.n_customers_with_signals,
        n_threads_processed=report.n_threads_processed,
        n_threads_failed=report.n_threads_failed,
        llm_calls=report.llm_calls,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

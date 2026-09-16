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
from datetime import UTC, datetime
from pathlib import Path

from config.loader import load_node3_config, load_vocabulary
from config.models import Node3Config, VocabularyConfig
from config.settings import get_settings
from node3.aggregate import aggregate_customer
from node3.llm_extractor import extract_thread_signals
from node3.preprocess import preprocess_threads
from node3.vocabulary import check_vocabulary_governance
from router.llm_mapper import LlmClient
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
    llm_client: LlmClient | None = None,
    vocabulary: VocabularyConfig | None = None,
    now: datetime | None = None,
) -> Node3Output:
    """Full Node 3 run: preprocess -> extract -> aggregate -> §3.11 output.

    Customers with no support data get an honest ``no_data`` record (confidence
    0.0, strength ``none``, summary ``None``) — never a fabricated signal.
    """
    now = now or datetime.now(UTC)
    vocab = vocabulary or load_vocabulary()

    requested = list(dict.fromkeys(customers))
    items, stats = preprocess_threads(list(support_data or []), config)

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

    warnings: list[str] = []
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
    all_flags = [flag for signals in customer_signals for flag in signals.risk_flags]
    warnings.extend(check_vocabulary_governance(all_flags, vocab))

    errors: list[dict[str, object]] = [*stats.errors]
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


def _usage() -> str:
    return (
        "churn-survival node3 <threads.json> [--customers <ids.csv|ids.json>] "
        "[--config <node3_version>] [--output <out.json>]"
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


def main(argv: list[str] | None = None) -> int:
    """CLI for ``churn-survival node3``."""
    args = list(sys.argv[1:] if argv is None else argv)
    config_version, output_path, customers_path = "1", None, None
    for flag in ("--config", "--output", "--customers"):
        if flag in args:
            index = args.index(flag)
            if index + 1 >= len(args):
                print(f"Usage: {_usage()}", file=sys.stderr)
                return 2
            value = args[index + 1]
            args = args[:index] + args[index + 2 :]
            if flag == "--config":
                config_version = value
            elif flag == "--output":
                output_path = value
            else:
                customers_path = value
    if len(args) != 1:
        print(f"Usage: {_usage()}", file=sys.stderr)
        return 2

    try:
        config = load_node3_config(config_version)
        vocabulary = load_vocabulary()
        raw = json.loads(Path(args[0]).read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("threads file must contain a JSON list of thread objects")
        customers = (
            _load_customer_universe(customers_path)
            if customers_path
            else _default_customer_universe(raw)
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
    if output_path:
        Path(output_path).write_text(
            json.dumps(output.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
        )
        print(f"Node 3: output written -> {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

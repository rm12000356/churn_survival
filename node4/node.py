"""Node 4 entry point — deterministic synthesis (architecture §4.18/§4.30).

``run_node4`` builds the customer union, computes quantitative and qualitative
values, combines them, evaluates critical rules, assigns risk levels and
confidence, generates structured reasons and evidence references, splits
insufficient-data customers, sorts, and assigns sequential ranks. Every union
customer appears exactly once in one of the two output lists.

The CLI reads previously produced ``Node2Output`` / ``Node3Output`` JSON files:
``churn-survival node4 [--node2 <file>] [--node3 <file>] [--config <v>] [--output <file>]``.
An omitted upstream flag means that node is unavailable (never fake success).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any

from config.loader import load_node4_config
from config.models import Node4Config
from node4.confidence import combined_confidence, quantitative_confidence
from node4.evidence import node2_evidence, node3_evidence
from node4.explain import build_explanation
from node4.qualitative import (
    qualitative_score,
    select_strongest,
    top_flags,
)
from node4.quantitative import quantitative_risk_score, top_drivers
from node4.ranking import rank_accounts, sort_insufficient_data_accounts
from node4.reasons import build_reasons
from node4.rules import classify_risk_level, evaluate_critical_rules, is_insufficient_data
from node4.scoring import combined_score
from schemas.enums import (
    CustomerState,
    HorizonStatus,
    ModelStatus,
    OverallSignalStrength,
    RiskLevel,
    SupportDataStatus,
    UrgencyLevel,
)
from schemas.node2 import Node2Output
from schemas.node3 import CustomerSupportSignals, Node3Output
from schemas.node4 import (
    EvidenceRefs,
    Node4Output,
    Node4ProcessingReport,
    QualitativeInfo,
    QuantitativeInfo,
    RankedAccount,
    RankedAccountMeta,
    SummaryStats,
)

WARNING_NODE3_UNAVAILABLE = "Node 3 unavailable; quantitative-only synthesis used."
WARNING_NODE2_UNAVAILABLE = "Node 2 unavailable; qualitative-only synthesis used."

_HORIZON_90D = "90d"


def _coerce[T](value: Any, model: type[T]) -> T | None:
    """Validate a mapping into the typed contract; typed instances pass through."""
    if value is None:
        return None
    if isinstance(value, model):
        return value
    return model.model_validate(value)


@dataclass
class _Alignment:
    """Node 2 full-index / scored-subset alignment (the two indexing systems)."""

    full_index: dict[str, int]
    scored_position: dict[str, int]
    states: list[CustomerState]
    risk_scores: list[float] | None
    survival_90_values: list[float] | None


def _build_alignment(node2: Node2Output) -> _Alignment:
    full_index: dict[str, int] = {}
    for index, customer_id in enumerate(node2.customer_ids):
        full_index.setdefault(customer_id, index)

    scored_position: dict[str, int] = {}
    position = 0
    for index, customer_id in enumerate(node2.customer_ids):
        if index < len(node2.customer_states) and (
            node2.customer_states[index] == CustomerState.SCORED
        ):
            scored_position.setdefault(customer_id, position)
            position += 1

    horizon = node2.survival_probabilities.get(_HORIZON_90D)
    values: list[float] | None = None
    if horizon is not None and horizon.status == HorizonStatus.AVAILABLE:
        values = list(horizon.values) if horizon.values is not None else None

    return _Alignment(
        full_index=full_index,
        scored_position=scored_position,
        states=list(node2.customer_states),
        risk_scores=node2.risk_scores,
        survival_90_values=values,
    )


def _node2_values(
    customer_id: str,
    node2: Node2Output | None,
    alignment: _Alignment | None,
) -> tuple[float | None, float | None, CustomerState]:
    """(survival_prob_90d, risk_score, customer_state) for one customer."""
    if node2 is None or alignment is None or customer_id not in alignment.full_index:
        return None, None, CustomerState.NOT_ENOUGH_DATA
    index = alignment.full_index[customer_id]
    state = (
        alignment.states[index]
        if index < len(alignment.states)
        else CustomerState.NOT_ENOUGH_DATA
    )
    if state != CustomerState.SCORED:
        return None, None, state
    position = alignment.scored_position.get(customer_id)
    if position is None:
        return None, None, state
    survival = None
    risk = None
    if alignment.survival_90_values is not None and position < len(
        alignment.survival_90_values
    ):
        survival = alignment.survival_90_values[position]
    if alignment.risk_scores is not None and position < len(alignment.risk_scores):
        risk = alignment.risk_scores[position]
    return survival, risk, state


def _index_signals(node3: Node3Output | None) -> dict[str, int]:
    index: dict[str, int] = {}
    if node3 is None:
        return index
    for position, signal in enumerate(node3.customer_signals):
        index.setdefault(signal.customer_id, position)
    return index


def _signal_for(
    customer_id: str,
    node3: Node3Output | None,
    index: Mapping[str, int],
) -> CustomerSupportSignals | None:
    if node3 is None or customer_id not in index:
        return None
    return node3.customer_signals[index[customer_id]]


def _thread_ids_by_customer(node3: Node3Output | None) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    if node3 is None:
        return mapping
    for thread in node3.thread_signals:
        ids = mapping.setdefault(thread.customer_id, [])
        if thread.thread_id not in ids:
            ids.append(thread.thread_id)
    return mapping


def _build_universe(
    node2: Node2Output | None,
    node3: Node3Output | None,
    errors: list[dict[str, Any]],
) -> list[str]:
    """Deterministic union; duplicates keep the first occurrence and record an error."""
    universe: list[str] = []
    seen: set[str] = set()

    if node2 is not None:
        node2_seen: set[str] = set()
        for customer_id in node2.customer_ids:
            if customer_id in node2_seen:
                errors.append(_duplicate_error(customer_id, "node2"))
                continue
            node2_seen.add(customer_id)
            if customer_id not in seen:
                seen.add(customer_id)
                universe.append(customer_id)

    if node3 is not None:
        node3_seen: set[str] = set()
        for signal in node3.customer_signals:
            customer_id = signal.customer_id
            if customer_id in node3_seen:
                errors.append(_duplicate_error(customer_id, "node3"))
                continue
            node3_seen.add(customer_id)
            if customer_id not in seen:
                seen.add(customer_id)
                universe.append(customer_id)

    return universe


def _duplicate_error(customer_id: str, source: str) -> dict[str, Any]:
    return {
        "code": "DUPLICATE_CUSTOMER",
        "customer_id": customer_id,
        "source": source,
        "message": (
            f"duplicate Node {source[-1]} customer record; keeping the first "
            "occurrence in input order"
        ),
    }


def _ranked_at(config: Node4Config) -> datetime:
    """D-4: derived from the declared reference date; never wall-clock time."""
    return datetime.combine(config.reference_date, time(0, 0), tzinfo=UTC)


def run_node4(
    node2_output: Node2Output | Mapping[str, Any] | None,
    node3_output: Node3Output | Mapping[str, Any] | None,
    config: Node4Config,
) -> Node4Output:
    """Full Node 4 synthesis (§4.18). Whole-input schema failure fails loudly."""
    node2 = _coerce(node2_output, Node2Output) if node2_output is not None else None
    node3 = _coerce(node3_output, Node3Output) if node3_output is not None else None
    if node2 is None and node3 is None:
        raise ValueError("Node 4 requires at least one of node2_output / node3_output")

    errors: list[dict[str, Any]] = []
    universe = _build_universe(node2, node3, errors)
    alignment = _build_alignment(node2) if node2 is not None else None
    signal_index = _index_signals(node3)
    thread_ids = _thread_ids_by_customer(node3)

    drivers = (
        top_drivers(node2.feature_associations, node2.model_type, config)
        if node2 is not None
        else []
    )
    model_status = node2.model_status if node2 is not None else ModelStatus.INSUFFICIENT_DATA
    model_version = node2.model_version if node2 is not None else ""

    main: list[RankedAccount] = []
    insufficient: list[RankedAccount] = []
    fallback_count = 0

    for customer_id in universe:
        survival_90, risk_score, customer_state = _node2_values(customer_id, node2, alignment)
        quantitative = quantitative_risk_score(survival_90, risk_score)
        if survival_90 is None and risk_score is not None and quantitative is not None:
            fallback_count += 1
        partial_alignment = (
            node2 is not None
            and customer_state == CustomerState.SCORED
            and quantitative is None
        )
        if partial_alignment:
            errors.append(
                {
                    "code": "PARTIAL_ALIGNMENT",
                    "customer_id": customer_id,
                    "source": "node2",
                    "message": (
                        "customer marked scored but has no usable risk score or 90d "
                        "survival value; quantitative treated as missing"
                    ),
                }
            )

        signal = _signal_for(customer_id, node3, signal_index)
        support_status = (
            signal.support_data_status if signal is not None else SupportDataStatus.NO_DATA
        )
        flags = list(signal.risk_flags) if signal is not None else []
        no_support_data = support_status == SupportDataStatus.NO_DATA
        # F-6: Node 3 guarantees `no_data` == zero threads in window (architecture
        # §3.8.6), so no support-derived field can legitimately accompany it. Treat
        # the combination as inconsistent upstream input (§4.26): record the
        # inconsistency when risk flags are present and trust the authoritative
        # status, so a provably spurious flag can never drive a Critical
        # classification or leak into ranking. All no-data support fields are
        # normalized to their Node 3 no-data values (zero/absent).
        if no_support_data and flags:
            errors.append(
                {
                    "code": "INCONSISTENT_SUPPORT_STATUS",
                    "customer_id": customer_id,
                    "source": "node3",
                    "message": (
                        "support_data_status is no_data but risk_flags are present; "
                        "treating the customer as having no support evidence"
                    ),
                }
            )
            flags = []
        strongest = select_strongest(flags, config)
        strength = (
            OverallSignalStrength(strongest.signal_strength.value)
            if strongest is not None
            else OverallSignalStrength.NONE
        )
        quality_score = qualitative_score(flags, support_status, config)
        quality_confidence = (
            0.0
            if signal is None or no_support_data
            else signal.overall_signal_confidence
        )
        churn_language = (
            False if signal is None or no_support_data else signal.churn_language_detected
        )
        escalation = False if signal is None or no_support_data else signal.escalation_signal
        urgency = (
            UrgencyLevel.UNKNOWN
            if signal is None or no_support_data
            else signal.urgency_level
        )

        combined = combined_score(quantitative, quality_score, strength, config)
        critical_rules = evaluate_critical_rules(quantitative, flags, config)
        insufficient_flag = is_insufficient_data(quantitative, support_status)
        level = (
            RiskLevel.INSUFFICIENT_DATA
            if insufficient_flag
            else classify_risk_level(combined, critical_rules, config)
        )
        confidence = combined_confidence(
            quantitative_confidence=(
                0.0 if partial_alignment else quantitative_confidence(model_status)
            ),
            qualitative_confidence=quality_confidence,
            config=config,
        )
        reasons = build_reasons(
            critical_rules=critical_rules,
            quantitative_score=quantitative,
            model_status=model_status,
            model_version=model_version,
            customer_state=customer_state,
            support_data_status=support_status,
            flags=flags,
            strongest=strongest,
            urgency_level=urgency,
            n_threads_in_window=signal.n_threads_in_window if signal is not None else 0,
            config=config,
        )
        evidence = EvidenceRefs(
            node2=node2_evidence(model_version, customer_state, drivers),
            node3=node3_evidence(signal, thread_ids.get(customer_id, [])),
        )

        account = RankedAccount(
            customer_id=customer_id,
            rank=None,
            combined_risk_level=level,
            combined_score=combined,
            combined_confidence=confidence,
            quantitative=QuantitativeInfo(
                model_status=model_status,
                risk_score=risk_score,
                survival_prob_90d=survival_90,
                normalized_risk=quantitative,
                top_drivers=drivers,
                customer_state=customer_state,
            ),
            qualitative=QualitativeInfo(
                support_data_status=support_status,
                signal_strength=strength,
                overall_signal_confidence=quality_confidence,
                churn_language_detected=churn_language,
                top_flags=top_flags(flags, config),
                escalation_signal=escalation,
            ),
            primary_reasons=reasons,
            evidence_refs=evidence,
            explanation=build_explanation(),
            meta=RankedAccountMeta(
                ranked_at=_ranked_at(config),
                ranking_version=config.ranking_version,
                threshold_version=config.threshold_version,
                critical_rules_version=config.critical_rules_version,
            ),
        )
        if insufficient_flag:
            insufficient.append(account)
        else:
            main.append(account)

    ranked_accounts = rank_accounts(main, config)
    insufficient_accounts = sort_insufficient_data_accounts(insufficient)

    warnings: list[str] = []
    if node3 is None:
        warnings.append(WARNING_NODE3_UNAVAILABLE)
    if node2 is None:
        warnings.append(WARNING_NODE2_UNAVAILABLE)
    if fallback_count:
        warnings.append(
            f"90d survival horizon unavailable; quantitative risk for {fallback_count} "
            "customer(s) uses the Node 2 risk_score fallback (reference time = median tenure)."
        )

    return Node4Output(
        ranked_accounts=ranked_accounts,
        insufficient_data_accounts=insufficient_accounts,
        summary_stats=SummaryStats(
            n_customers=len(ranked_accounts) + len(insufficient_accounts),
            n_critical=sum(
                1
                for account in ranked_accounts
                if account.combined_risk_level == RiskLevel.CRITICAL
            ),
            n_high=sum(
                1 for account in ranked_accounts if account.combined_risk_level == RiskLevel.HIGH
            ),
            n_medium=sum(
                1 for account in ranked_accounts if account.combined_risk_level == RiskLevel.MEDIUM
            ),
            n_low=sum(
                1 for account in ranked_accounts if account.combined_risk_level == RiskLevel.LOW
            ),
            n_insufficient_data=len(insufficient_accounts),
        ),
        reference_date=config.reference_date,
        processing_report=Node4ProcessingReport(warnings=warnings, errors=errors),
    )


def _usage() -> str:
    return (
        "churn-survival node4 [--node2 <node2_output.json>] [--node3 <node3_output.json>] "
        "[--config <node4_version>] [--output <out.json>]"
    )


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    """CLI for ``churn-survival node4`` (reads saved Node 2 / Node 3 outputs)."""
    args = list(sys.argv[1:] if argv is None else argv)
    node2_path: str | None = None
    node3_path: str | None = None
    output_path: str | None = None
    config_version = "1"

    for flag in ("--node2", "--node3", "--config", "--output"):
        if flag in args:
            index = args.index(flag)
            if index + 1 >= len(args):
                print(f"Usage: {_usage()}", file=sys.stderr)
                return 2
            value = args[index + 1]
            args = args[:index] + args[index + 2 :]
            if flag == "--node2":
                node2_path = value
            elif flag == "--node3":
                node3_path = value
            elif flag == "--config":
                config_version = value
            else:
                output_path = value

    if args or (node2_path is None and node3_path is None):
        print(f"Usage: {_usage()}", file=sys.stderr)
        return 2

    try:
        config = load_node4_config(config_version)
        node2_raw = _load_json(node2_path) if node2_path is not None else None
        node3_raw = _load_json(node3_path) if node3_path is not None else None
        output = run_node4(node2_raw, node3_raw, config)
    except Exception as exc:  # noqa: BLE001 - CLI boundary must fail loudly
        print(f"ERROR: Node 4 failed: {exc}", file=sys.stderr)
        return 1

    stats = output.summary_stats
    print(
        f"Node 4: customers={stats.n_customers} critical={stats.n_critical} "
        f"high={stats.n_high} medium={stats.n_medium} low={stats.n_low} "
        f"insufficient={stats.n_insufficient_data}"
    )
    if output_path is not None:
        Path(output_path).write_text(
            json.dumps(output.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8"
        )
        print(f"Node 4: output written -> {output_path}")
    from logging_setup import emit_node_completion

    emit_node_completion(
        "node4",
        config_version=config_version,
        n_customers=stats.n_customers,
        n_critical=stats.n_critical,
        n_high=stats.n_high,
        n_insufficient=stats.n_insufficient_data,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

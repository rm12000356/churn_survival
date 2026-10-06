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
from node4.confidence import (
    combined_confidence,
    customer_quant_confidence,
    quantitative_confidence,
)
from node4.evidence import node2_evidence, node3_evidence, signal_version
from node4.explain import build_explanation
from node4.qualitative import (
    qualitative_score,
    select_strongest,
    top_flags,
)
from node4.quantitative import (
    driver_details_for_customer,
    lift_normalized_risk,
    quantitative_risk_score,
    top_drivers,
)
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
from schemas.node2 import DriverDetail, FeatureContribution, Node2Output
from schemas.node3 import CustomerSupportSignals, Node3Output
from schemas.node4 import (
    ChurnedAccount,
    ConfidenceFactorsOut,
    EvidenceRefs,
    ForwardStatus,
    Node4Output,
    Node4ProcessingReport,
    Node4Provenance,
    QualitativeInfo,
    QuantitativeInfo,
    RankedAccount,
    RankedAccountMeta,
    SummaryStats,
)

WARNING_NODE3_UNAVAILABLE = "Node 3 unavailable; quantitative-only synthesis used."
WARNING_SUPPORT_NOT_SUPPLIED = (
    "No support threads were supplied for this run; synthesis is quantitative-only "
    "(score and confidence come from the survival model alone)."
)
WARNING_NODE2_UNAVAILABLE = "Node 2 unavailable; qualitative-only synthesis used."
WARNING_NO_FORWARD_SURVIVAL = (
    "Node 2 output has no forward 90-day survival; the absolute risk scale "
    "(risk_norm_v1) was used instead of the lift scale."
)

_HORIZON_90D = "90d"


def _coerce[T](value: Any, model: type[T]) -> T | None:
    if value is None:
        return None
    if isinstance(value, model):
        return value
    return model.model_validate(value)


@dataclass
class _Alignment:
    full_index: dict[str, int]
    scored_position: dict[str, int]
    states: list[CustomerState]
    risk_scores: list[float] | None
    survival_90_values: list[float] | None
    forward_90_values: list[float | None] | None = None
    forward_90_ci: list[list[float | None]] | None = None
    tenure_days: list[float] | None = None
    event_observed: list[int] | None = None
    contributions: list[list[FeatureContribution]] | None = None
    relative_log_hazard: list[float | None] | None = None


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

    forward = (node2.forward_survival or {}).get(_HORIZON_90D)
    return _Alignment(
        full_index=full_index,
        scored_position=scored_position,
        states=list(node2.customer_states),
        risk_scores=node2.risk_scores,
        survival_90_values=values,
        forward_90_values=list(forward.values) if forward is not None else None,
        forward_90_ci=list(forward.ci) if forward is not None else None,
        tenure_days=node2.customer_tenure_days,
        event_observed=node2.customer_event_observed,
        contributions=node2.customer_contributions,
        relative_log_hazard=node2.customer_relative_log_hazard,
    )


def _node2_drivers_for(
    customer_id: str,
    alignment: _Alignment | None,
    model_drivers: list[str],
    config: Node4Config,
) -> tuple[list[str], list[DriverDetail], float | None]:
    if not config.per_customer_drivers or alignment is None or alignment.contributions is None:
        return model_drivers, [], None
    position = alignment.scored_position.get(customer_id)
    if position is None or position >= len(alignment.contributions):
        return [], [], None
    details = driver_details_for_customer(alignment.contributions[position], config)
    relative = (
        alignment.relative_log_hazard[position]
        if alignment.relative_log_hazard is not None
        and position < len(alignment.relative_log_hazard)
        else None
    )
    return [detail.feature for detail in details], details, relative


@dataclass(frozen=True)
class _Forward:
    tenure_days: float | None = None
    event_observed: int | None = None
    present: bool = False
    survival: float | None = None
    ci_width: float | None = None


def _node2_forward(customer_id: str, alignment: _Alignment | None) -> _Forward:
    if alignment is None or customer_id not in alignment.full_index:
        return _Forward()
    index = alignment.full_index[customer_id]
    tenure = (
        alignment.tenure_days[index]
        if alignment.tenure_days is not None and index < len(alignment.tenure_days)
        else None
    )
    event = (
        alignment.event_observed[index]
        if alignment.event_observed is not None and index < len(alignment.event_observed)
        else None
    )
    position = alignment.scored_position.get(customer_id)
    values = alignment.forward_90_values
    if position is None or values is None or position >= len(values):
        return _Forward(tenure_days=tenure, event_observed=event)
    survival = values[position]
    width = None
    if alignment.forward_90_ci is not None and position < len(alignment.forward_90_ci):
        bounds = alignment.forward_90_ci[position]
        if len(bounds) == 2 and bounds[0] is not None and bounds[1] is not None:
            width = max(0.0, float(bounds[1]) - float(bounds[0]))
    return _Forward(
        tenure_days=tenure,
        event_observed=event,
        present=True,
        survival=survival,
        ci_width=width,
    )


def _base_rate(
    universe: list[str],
    alignment: _Alignment | None,
) -> tuple[float | None, int]:
    if alignment is None:
        return None, 0
    probabilities: list[float] = []
    for customer_id in universe:
        forward = _node2_forward(customer_id, alignment)
        if forward.survival is not None and forward.event_observed != 1:
            probabilities.append(1.0 - forward.survival)
    if not probabilities:
        return None, 0
    return sum(probabilities) / len(probabilities), len(probabilities)


def _node2_values(
    customer_id: str,
    node2: Node2Output | None,
    alignment: _Alignment | None,
) -> tuple[float | None, float | None, CustomerState]:
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
    return datetime.combine(config.reference_date, time(0, 0), tzinfo=UTC)


def run_node4(
    node2_output: Node2Output | Mapping[str, Any] | None,
    node3_output: Node3Output | Mapping[str, Any] | None,
    config: Node4Config,
    *,
    support_supplied: bool | None = None,
) -> Node4Output:
    node2 = _coerce(node2_output, Node2Output) if node2_output is not None else None
    node3 = _coerce(node3_output, Node3Output) if node3_output is not None else None
    if node2 is None and node3 is None:
        raise ValueError("Node 4 requires at least one of node2_output / node3_output")
    if support_supplied is None:
        support_supplied = node3 is not None and node3.processing_report.n_threads_processed > 0
    quantitative_only = (
        config.quantitative_only_without_support and not support_supplied and node2 is not None
    )

    errors: list[dict[str, Any]] = []
    universe = _build_universe(node2, node3, errors)
    alignment = _build_alignment(node2) if node2 is not None else None
    signal_index = _index_signals(node3)
    thread_ids = _thread_ids_by_customer(node3)

    model_drivers = (
        top_drivers(node2.feature_associations, node2.model_type, config)
        if node2 is not None
        else []
    )
    model_status = node2.model_status if node2 is not None else ModelStatus.INSUFFICIENT_DATA
    model_version = node2.model_version if node2 is not None else ""
    node3_signal_version = (
        signal_version(node3.customer_signals[0].meta)
        if node3 is not None and node3.customer_signals
        else ""
    )

    main: list[RankedAccount] = []
    insufficient: list[RankedAccount] = []
    churned: list[ChurnedAccount] = []
    fallback_count = 0
    beyond_follow_up_count = 0
    warnings: list[str] = []

    use_lift = False
    base_rate: float | None = None
    n_base = 0
    if config.risk_scale == "lift" and node2 is not None:
        if alignment is None or alignment.forward_90_values is None:
            warnings.append(WARNING_NO_FORWARD_SURVIVAL)
        else:
            base_rate, n_base = _base_rate(universe, alignment)
            if base_rate is None or base_rate <= 0.0 or n_base < config.base_rate_min_customers:
                warnings.append(
                    f"Lift scale needs at least {config.base_rate_min_customers} active scored "
                    f"customers with a positive forward 90-day churn base rate (got {n_base}); "
                    "the absolute risk scale (risk_norm_v1) was used instead."
                )
                base_rate = None
            else:
                use_lift = True

    for customer_id in universe:
        survival_90, risk_score, customer_state = _node2_values(customer_id, node2, alignment)
        forward = _node2_forward(customer_id, alignment)
        drivers, driver_details, relative_log_hazard = _node2_drivers_for(
            customer_id, alignment, model_drivers, config
        )
        if config.separate_churned and forward.event_observed == 1:
            churned.append(
                ChurnedAccount(
                    customer_id=customer_id,
                    tenure_days=forward.tenure_days,
                    evidence_refs=node2_evidence(model_version, customer_state, drivers),
                )
            )
            continue

        quantitative: float | None
        churn_prob: float | None = None
        lift: float | None = None
        forward_status: ForwardStatus | None = None
        if use_lift and base_rate is not None:
            if forward.survival is not None:
                churn_prob = 1.0 - forward.survival
                quantitative, lift = lift_normalized_risk(
                    churn_prob, base_rate, config.lift_points or []
                )
                forward_status = "available"
            else:
                quantitative = None
                if forward.present and forward.event_observed != 1:
                    forward_status = "beyond_follow_up"
                    beyond_follow_up_count += 1
                else:
                    forward_status = "unavailable"
        else:
            quantitative = quantitative_risk_score(survival_90, risk_score)
            if survival_90 is None and risk_score is not None and quantitative is not None:
                fallback_count += 1
        partial_alignment = (
            node2 is not None
            and customer_state == CustomerState.SCORED
            and quantitative is None
            and not (use_lift and forward.present)
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

        combined = combined_score(
            quantitative, quality_score, strength, config, quantitative_only=quantitative_only
        )
        critical_rules = evaluate_critical_rules(quantitative, flags, config)
        insufficient_flag = is_insufficient_data(quantitative, support_status)
        level = (
            RiskLevel.INSUFFICIENT_DATA
            if insufficient_flag
            else classify_risk_level(combined, critical_rules, config)
        )
        factors_out: ConfidenceFactorsOut | None = None
        if config.confidence_factors is not None:
            quant_conf, model_f, precision_f, history_f = customer_quant_confidence(
                model_status, forward.ci_width, forward.tenure_days, config.confidence_factors
            )
            if partial_alignment or quantitative is None:
                quant_conf, precision_f = 0.0, 0.0
            factors_out = ConfidenceFactorsOut(
                model=model_f,
                precision=precision_f,
                history=history_f,
                quantitative=quant_conf,
                support=None if quantitative_only else quality_confidence,
            )
        else:
            quant_conf = 0.0 if partial_alignment else quantitative_confidence(model_status)
        confidence = combined_confidence(
            quantitative_confidence=quant_conf,
            qualitative_confidence=quality_confidence,
            config=config,
            quantitative_only=quantitative_only,
        )
        quant_extra: dict[str, Any] | None = None
        if use_lift:
            quant_extra = {
                "churn_prob_90d_forward": (
                    round(churn_prob, 6) if churn_prob is not None else None
                ),
                "lift_vs_base": lift,
                "base_rate_90d": round(base_rate, 6) if base_rate is not None else None,
                "forward_status": forward_status,
            }
        if driver_details:
            quant_extra = {**(quant_extra or {}), "drivers": list(drivers)}
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
            quantitative_only=quantitative_only,
            quant_extra=quant_extra,
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
                churn_prob_90d_forward=(
                    round(churn_prob, 6) if churn_prob is not None else None
                ),
                lift_vs_base=lift,
                forward_status=forward_status,
                driver_details=driver_details,
                relative_log_hazard=relative_log_hazard,
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
            confidence_factors=factors_out,
        )
        if insufficient_flag:
            insufficient.append(account)
        else:
            main.append(account)

    ranked_accounts = rank_accounts(main, config)
    insufficient_accounts = sort_insufficient_data_accounts(insufficient)

    if quantitative_only:
        warnings.append(WARNING_SUPPORT_NOT_SUPPLIED)
    elif node3 is None:
        warnings.append(WARNING_NODE3_UNAVAILABLE)
    if node2 is None:
        warnings.append(WARNING_NODE2_UNAVAILABLE)
    if fallback_count:
        warnings.append(
            f"90d survival horizon unavailable; quantitative risk for {fallback_count} "
            "customer(s) uses the Node 2 risk_score fallback (reference time = median tenure)."
        )
    if use_lift and base_rate is not None:
        warnings.append(
            "Quantitative risk is lift-scaled: forward 90-day churn probability relative to "
            f"the book average of {base_rate:.4f} over {n_base} active scored customer(s)."
        )
    if beyond_follow_up_count:
        warnings.append(
            f"{beyond_follow_up_count} active customer(s) have tenure beyond the model's "
            "follow-up for a 90-day forward window; their quantitative risk is treated as "
            "missing (never low)."
        )
    if churned:
        warnings.append(
            f"{len(churned)} customer(s) already churned; listed separately in "
            "churned_accounts and not ranked."
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
            n_churned=len(churned),
        ),
        churned_accounts=churned,
        reference_date=config.reference_date,
        processing_report=Node4ProcessingReport(warnings=warnings, errors=errors),
        provenance=Node4Provenance(
            node2_model_version=model_version,
            node3_signal_version=node3_signal_version,
            ranking_version=config.ranking_version,
            threshold_version=config.threshold_version,
            critical_rules_version=config.critical_rules_version,
        ),
    )


def _usage() -> str:
    return (
        "churn-survival node4 [--node2 <node2_output.json>] [--node3 <node3_output.json>] "
        "[--config <node4_version>] [--output <out.json>]"
    )


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    node2_path: str | None = None
    node3_path: str | None = None
    output_path: str | None = None
    config_version = "4"

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

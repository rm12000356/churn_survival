from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from config.models import Node4Config
from node4.evidence import flag_evidence
from node4.rules import is_significant
from schemas.enums import (
    CustomerState,
    FlagType,
    ModelStatus,
    OverallSignalStrength,
    ReasonType,
    Severity,
    SignalStrength,
    SupportDataStatus,
    UrgencyLevel,
)
from schemas.node3 import AggregatedRiskFlag
from schemas.node4 import StructuredReason

_MEANINGFUL_STRENGTHS = {SignalStrength.MODERATE, SignalStrength.STRONG}


def _reason(
    reason_type: ReasonType,
    source: str,
    severity: str,
    evidence_ref: str | dict[str, Any],
) -> StructuredReason:
    return StructuredReason(
        reason_type=reason_type,
        source=source,  # type: ignore[arg-type]
        severity=severity,
        evidence_ref=evidence_ref,
    )


def _quant_evidence(
    model_version: str,
    model_status: ModelStatus,
    customer_state: CustomerState,
    quantitative_score: float | None,
) -> dict[str, Any]:
    return {
        "model_version": model_version,
        "model_status": model_status.value,
        "customer_state": customer_state.value,
        "normalized_risk": quantitative_score,
    }


def _support_evidence(support_data_status: SupportDataStatus, n_threads: int) -> dict[str, Any]:
    return {
        "support_data_status": support_data_status.value,
        "n_threads_in_window": n_threads,
    }


def _repeated_flags(flags: Sequence[AggregatedRiskFlag]) -> list[AggregatedRiskFlag]:
    return sorted(
        (
            flag
            for flag in flags
            if flag.recurrence_count >= 2 and flag.flag_type != FlagType.POSITIVE_FEEDBACK
        ),
        key=lambda flag: flag.flag_type.value,
    )


def build_reasons(
    *,
    critical_rules: Sequence[ReasonType],
    quantitative_score: float | None,
    model_status: ModelStatus,
    model_version: str,
    customer_state: CustomerState,
    support_data_status: SupportDataStatus,
    flags: Sequence[AggregatedRiskFlag],
    strongest: AggregatedRiskFlag | None,
    urgency_level: UrgencyLevel,
    n_threads_in_window: int,
    config: Node4Config,
    quantitative_only: bool = False,
    quant_extra: dict[str, Any] | None = None,
) -> list[StructuredReason]:
    reasons: list[StructuredReason] = []
    if strongest is not None and strongest.flag_type == FlagType.POSITIVE_FEEDBACK:
        strongest = None

    cancellation = [f for f in flags if f.flag_type == FlagType.CANCELLATION_INTENT]
    for rule in critical_rules:
        if rule == ReasonType.CRITICAL_CANCELLATION_INTENT:
            flag = next(f for f in cancellation if f.signal_strength in _MEANINGFUL_STRENGTHS)
            reasons.append(_reason(rule, "node3", "critical", flag_evidence(flag)))
        elif rule == ReasonType.CRITICAL_CANCELLATION_PLUS_SIGNIFICANT_FLAG:
            flag = next(f for f in cancellation if f.signal_strength == SignalStrength.STRONG)
            ref = flag_evidence(flag)
            ref["other_flag_types"] = sorted(
                {
                    f.flag_type.value
                    for f in flags
                    if is_significant(f) and f.flag_type != FlagType.CANCELLATION_INTENT
                }
            )
            reasons.append(_reason(rule, "node3", "critical", ref))
        elif rule == ReasonType.CRITICAL_HIGH_QUANT_PLUS_CONTRACT_CONCERN:
            flag = next(
                f
                for f in flags
                if f.flag_type == FlagType.RENEWAL_OR_CONTRACT_CONCERN
                and f.signal_strength == SignalStrength.STRONG
            )
            ref = flag_evidence(flag)
            ref["normalized_risk"] = quantitative_score
            reasons.append(_reason(rule, "node3", "critical", ref))
        elif rule == ReasonType.CRITICAL_REPEATED_HIGH_SEVERITY_PLUS_HIGH_QUANT:
            repeated = [
                f for f in _repeated_flags(flags) if f.severity == Severity.HIGH
            ]
            ref = {
                "flags": [
                    {"flag_type": f.flag_type.value, "recurrence_count": f.recurrence_count}
                    for f in repeated
                ],
                "normalized_risk": quantitative_score,
            }
            reasons.append(_reason(rule, "node3", "critical", ref))

    quant_ref = _quant_evidence(model_version, model_status, customer_state, quantitative_score)
    if quant_extra:
        quant_ref.update(quant_extra)
    if quantitative_score is None:
        reasons.append(_reason(ReasonType.MISSING_QUANTITATIVE_DATA, "node2", "low", quant_ref))
    elif quantitative_score >= config.quantitative_thresholds.high:
        reasons.append(_reason(ReasonType.HIGH_QUANTITATIVE_RISK, "node2", "high", quant_ref))
    elif quantitative_score >= config.quantitative_thresholds.medium:
        reasons.append(_reason(ReasonType.MODERATE_QUANTITATIVE_RISK, "node2", "medium", quant_ref))

    support_ref = _support_evidence(support_data_status, n_threads_in_window)
    if support_data_status == SupportDataStatus.NO_DATA:
        if not quantitative_only:
            reasons.append(_reason(ReasonType.MISSING_SUPPORT_DATA, "node3", "low", support_ref))
    elif support_data_status == SupportDataStatus.LIMITED_DATA:
        reasons.append(_reason(ReasonType.LIMITED_SUPPORT_DATA, "node3", "low", support_ref))

    if strongest is not None:
        if strongest.signal_strength == SignalStrength.STRONG:
            reasons.append(
                _reason(ReasonType.STRONG_SUPPORT_SIGNAL, "node3", "high", flag_evidence(strongest))
            )
        elif strongest.signal_strength == SignalStrength.MODERATE:
            reasons.append(
                _reason(
                    ReasonType.MODERATE_SUPPORT_SIGNAL, "node3", "medium", flag_evidence(strongest)
                )
            )

    repeated = _repeated_flags(flags)
    if repeated:
        repeated_ref = {
            "flags": [
                {"flag_type": f.flag_type.value, "recurrence_count": f.recurrence_count}
                for f in repeated
            ],
            "max_recurrence_count": max(f.recurrence_count for f in repeated),
        }
        reasons.append(
            _reason(ReasonType.REPEATED_SUPPORT_ISSUE, "node3", "high", repeated_ref)
        )

    if urgency_level == UrgencyLevel.HIGH:
        reasons.append(
            _reason(
                ReasonType.HIGH_SUPPORT_URGENCY,
                "node3",
                "high",
                {"urgency_level": urgency_level.value},
            )
        )

    q_high = config.quantitative_thresholds.high
    q_medium = config.quantitative_thresholds.medium
    has_positive = any(f.flag_type == FlagType.POSITIVE_FEEDBACK for f in flags)
    strong_cancellation = any(
        f.flag_type == FlagType.CANCELLATION_INTENT and f.signal_strength in _MEANINGFUL_STRENGTHS
        for f in flags
    )
    conflict = (
        quantitative_score is not None and quantitative_score >= q_high and has_positive
    ) or (
        quantitative_score is not None and quantitative_score < q_medium and strong_cancellation
    ) or (
        not quantitative_only
        and quantitative_score is not None
        and quantitative_score >= q_high
        and support_data_status == SupportDataStatus.NO_DATA
    )
    cross_ref = {
        "normalized_risk": quantitative_score,
        "strongest_signal_strength": (
            strongest.signal_strength.value
            if strongest is not None
            else OverallSignalStrength.NONE.value
        ),
        "support_data_status": support_data_status.value,
    }
    if conflict:
        reasons.append(
            _reason(ReasonType.QUANTITATIVE_QUALITATIVE_CONFLICT, "node2", "medium", cross_ref)
        )
    elif (
        quantitative_score is not None
        and quantitative_score >= q_high
        and strongest is not None
        and strongest.signal_strength == SignalStrength.STRONG
    ):
        reasons.append(
            _reason(ReasonType.QUANTITATIVE_QUALITATIVE_AGREEMENT, "node2", "low", cross_ref)
        )

    return reasons


_REASON_TEXT: dict[ReasonType, str] = {
    ReasonType.CRITICAL_CANCELLATION_INTENT: (
        "Explicit cancellation intent with moderate or strong signal strength."
    ),
    ReasonType.CRITICAL_CANCELLATION_PLUS_SIGNIFICANT_FLAG: (
        "Strong cancellation intent alongside another significant risk flag."
    ),
    ReasonType.CRITICAL_HIGH_QUANT_PLUS_CONTRACT_CONCERN: (
        "Very high quantitative risk combined with strong renewal or contract concern."
    ),
    ReasonType.CRITICAL_REPEATED_HIGH_SEVERITY_PLUS_HIGH_QUANT: (
        "Repeated high-severity support problems combined with very high quantitative risk."
    ),
    ReasonType.HIGH_QUANTITATIVE_RISK: "High quantitative churn risk.",
    ReasonType.MODERATE_QUANTITATIVE_RISK: "Moderate quantitative churn risk.",
    ReasonType.STRONG_SUPPORT_SIGNAL: "Strong qualitative support signal.",
    ReasonType.MODERATE_SUPPORT_SIGNAL: "Moderate qualitative support signal.",
    ReasonType.REPEATED_SUPPORT_ISSUE: "Repeated support issue reported by the customer.",
    ReasonType.HIGH_SUPPORT_URGENCY: "High support urgency.",
    ReasonType.LIMITED_SUPPORT_DATA: "Limited support data available.",
    ReasonType.MISSING_SUPPORT_DATA: "No support data available.",
    ReasonType.MISSING_QUANTITATIVE_DATA: "No quantitative risk score available.",
    ReasonType.QUANTITATIVE_QUALITATIVE_AGREEMENT: (
        "Quantitative and qualitative evidence agree."
    ),
    ReasonType.QUANTITATIVE_QUALITATIVE_CONFLICT: (
        "Quantitative and qualitative evidence conflict."
    ),
}


def format_reason(reason: StructuredReason) -> str:
    text = _REASON_TEXT[reason.reason_type]
    return f"{text} (source: {reason.source}; severity: {reason.severity})"

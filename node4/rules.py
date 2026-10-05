from __future__ import annotations

from collections.abc import Sequence

from config.models import Node4Config
from schemas.enums import (
    FlagType,
    ReasonType,
    RiskLevel,
    Severity,
    SignalStrength,
    SupportDataStatus,
)
from schemas.node3 import AggregatedRiskFlag

_MEANINGFUL_STRENGTHS = {SignalStrength.MODERATE, SignalStrength.STRONG}


def is_significant(flag: AggregatedRiskFlag) -> bool:
    if flag.flag_type == FlagType.POSITIVE_FEEDBACK:
        return False
    return (
        flag.severity in {Severity.MEDIUM, Severity.HIGH}
        or flag.signal_strength in _MEANINGFUL_STRENGTHS
    )


def evaluate_critical_rules(
    quantitative_score: float | None,
    flags: Sequence[AggregatedRiskFlag],
    config: Node4Config,
) -> list[ReasonType]:
    rules: list[ReasonType] = []
    flags = [f for f in flags if f.flag_type != FlagType.POSITIVE_FEEDBACK]
    high_quant = (
        quantitative_score is not None
        and quantitative_score >= config.quantitative_thresholds.high
    )

    cancellation = [f for f in flags if f.flag_type == FlagType.CANCELLATION_INTENT]
    if any(f.signal_strength in _MEANINGFUL_STRENGTHS for f in cancellation):
        rules.append(ReasonType.CRITICAL_CANCELLATION_INTENT)

    strong_cancellation = any(f.signal_strength == SignalStrength.STRONG for f in cancellation)
    other_significant = any(
        is_significant(f) and f.flag_type != FlagType.CANCELLATION_INTENT for f in flags
    )
    if strong_cancellation and other_significant:
        rules.append(ReasonType.CRITICAL_CANCELLATION_PLUS_SIGNIFICANT_FLAG)

    strong_contract = any(
        f.flag_type == FlagType.RENEWAL_OR_CONTRACT_CONCERN
        and f.signal_strength == SignalStrength.STRONG
        for f in flags
    )
    if high_quant and strong_contract:
        rules.append(ReasonType.CRITICAL_HIGH_QUANT_PLUS_CONTRACT_CONCERN)

    repeated_high_severity = any(
        f.severity == Severity.HIGH and f.recurrence_count >= 2 for f in flags
    )
    if high_quant and repeated_high_severity:
        rules.append(ReasonType.CRITICAL_REPEATED_HIGH_SEVERITY_PLUS_HIGH_QUANT)

    return rules


def classify_risk_level(
    combined: float,
    critical_rules: Sequence[ReasonType],
    config: Node4Config,
) -> RiskLevel:
    if critical_rules:
        return RiskLevel.CRITICAL
    if combined >= config.risk_thresholds.high:
        return RiskLevel.HIGH
    if combined >= config.risk_thresholds.medium:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def is_insufficient_data(
    quantitative_score: float | None,
    support_data_status: SupportDataStatus,
) -> bool:
    return quantitative_score is None and support_data_status == SupportDataStatus.NO_DATA

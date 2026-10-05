from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from config.models import Node4Config
from schemas.enums import FlagType, OverallSignalStrength, SupportDataStatus
from schemas.node3 import AggregatedRiskFlag


def _risk_flags(flags: Sequence[AggregatedRiskFlag]) -> list[AggregatedRiskFlag]:
    return [flag for flag in flags if flag.flag_type != FlagType.POSITIVE_FEEDBACK]


def _strength_order(flag: AggregatedRiskFlag, config: Node4Config) -> int:
    return config.strength_order[OverallSignalStrength(flag.signal_strength.value)]


def _selection_key(flag: AggregatedRiskFlag, config: Node4Config) -> tuple[int, float, int, str]:
    return (
        -_strength_order(flag, config),
        -config.hierarchy_weights[flag.flag_type],
        -flag.recurrence_count,
        flag.flag_type.value,
    )


def select_strongest(
    flags: Sequence[AggregatedRiskFlag],
    config: Node4Config,
) -> AggregatedRiskFlag | None:
    candidates = _risk_flags(flags)
    if not candidates:
        return None
    return min(candidates, key=lambda flag: _selection_key(flag, config))


def strongest_signal_strength(
    flags: Sequence[AggregatedRiskFlag],
    config: Node4Config,
) -> OverallSignalStrength:
    strongest = select_strongest(flags, config)
    if strongest is None:
        return OverallSignalStrength.NONE
    return OverallSignalStrength(strongest.signal_strength.value)


def recurrence_bonus(max_recurrence_count: int) -> float:
    return max(0.0, min(0.20, 0.05 * max(0, max_recurrence_count - 1)))


def qualitative_score(
    flags: Sequence[AggregatedRiskFlag],
    support_data_status: SupportDataStatus,
    config: Node4Config,
) -> float:
    if support_data_status == SupportDataStatus.NO_DATA:
        return 0.0
    candidates = _risk_flags(flags)
    if not candidates:
        return 0.0
    score = 0.0
    for flag in candidates:
        flag_score = (
            config.hierarchy_weights[flag.flag_type] * config.strength_scores[flag.signal_strength]
        )
        score = max(score, flag_score)
    max_recurrence = max(flag.recurrence_count for flag in candidates)
    return min(1.0, score + recurrence_bonus(max_recurrence))


def top_flags(
    flags: Sequence[AggregatedRiskFlag],
    config: Node4Config,
) -> list[dict[str, Any]]:
    ordered = sorted(_risk_flags(flags), key=lambda flag: _selection_key(flag, config))
    return [
        {
            "flag_type": flag.flag_type.value,
            "severity": flag.severity.value,
            "signal_strength": flag.signal_strength.value,
            "recurrence_count": flag.recurrence_count,
            "confidence": flag.confidence,
        }
        for flag in ordered
    ]

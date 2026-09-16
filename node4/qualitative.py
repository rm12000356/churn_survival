"""Node 4 qualitative scoring (architecture §4.5/§4.6, ROADMAP Tasks 5.4/5.5).

Consumes Node 3's already aggregated customer-level flags. Node 4 does **not**
reapply Node 3's exponential recency decay; it takes the strongest flag's
``hierarchy_weight * strength_score`` and adds the locked recurrence bonus.

``positive_feedback`` is contextual only (architecture §4.2/§4.29): it never
contributes to the base score, is excluded from the recurrence count, from
strongest-risk-signal selection, and from ``top_flags`` (a risk-evidence view).
The original flag remains available upstream and in the evidence references.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from config.models import Node4Config
from schemas.enums import FlagType, OverallSignalStrength, SupportDataStatus
from schemas.node3 import AggregatedRiskFlag


def _risk_flags(flags: Sequence[AggregatedRiskFlag]) -> list[AggregatedRiskFlag]:
    """Flags that may carry risk semantics; positive feedback is contextual only."""
    return [flag for flag in flags if flag.flag_type != FlagType.POSITIVE_FEEDBACK]


def _strength_order(flag: AggregatedRiskFlag, config: Node4Config) -> int:
    return config.strength_order[OverallSignalStrength(flag.signal_strength.value)]


def _selection_key(flag: AggregatedRiskFlag, config: Node4Config) -> tuple[int, float, int, str]:
    """§4.6 ordering: strength desc, hierarchy weight desc, recurrence desc, type asc."""
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
    """§4.6 selection over risk-relevant flags only (positive feedback excluded)."""
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
    """Locked §4.5.1 bonus: 1→0.00, 2→0.05, 3→0.10, 4→0.15, 5+→0.20 (unrounded)."""
    return max(0.0, min(0.20, 0.05 * max(0, max_recurrence_count - 1)))


def qualitative_score(
    flags: Sequence[AggregatedRiskFlag],
    support_data_status: SupportDataStatus,
    config: Node4Config,
) -> float:
    """Base strongest-flag score plus recurrence bonus, clamped to [0, 1].

    No intermediate rounding: the contract does not authorize it, and the
    combined score must see the exact value.
    """
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
    """Risk-evidence flag summary ordered strongest-first (positive excluded)."""
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

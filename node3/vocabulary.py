from __future__ import annotations

from collections.abc import Sequence

from config.loader import load_vocabulary
from config.models import VocabularyConfig
from schemas.enums import FlagType, SignalStrength
from schemas.node3 import AggregatedRiskFlag, RiskFlag

STRENGTH_SCORE: dict[SignalStrength, int] = {
    SignalStrength.WEAK: 1,
    SignalStrength.MODERATE: 2,
    SignalStrength.STRONG: 3,
}

NON_PRIORITY_RANK = 999


def get_vocabulary() -> VocabularyConfig:
    return load_vocabulary()


def get_hierarchy_rank(
    flag_type: FlagType | str,
    vocabulary: VocabularyConfig | None = None,
) -> int:
    vocab = vocabulary or get_vocabulary()
    key = flag_type if isinstance(flag_type, FlagType) else FlagType(flag_type)
    rank = vocab.ranks.get(key)
    return NON_PRIORITY_RANK if rank is None else rank


def check_vocabulary_governance(
    flags: Sequence[RiskFlag | AggregatedRiskFlag],
    vocabulary: VocabularyConfig | None = None,
) -> list[str]:
    vocab = vocabulary or get_vocabulary()
    if not flags:
        return []
    other = sum(1 for f in flags if f.flag_type is FlagType.OTHER)
    if other == 0:
        return []
    share_pct = 100.0 * other / len(flags)
    if share_pct > vocab.governance.other_review_threshold_pct:
        return [
            f"vocabulary governance: 'other' is {share_pct:.1f}% of flags "
            f"(threshold {vocab.governance.other_review_threshold_pct:.1f}%) — "
            f"review within {vocab.governance.review_interval_weeks} weeks"
        ]
    return []

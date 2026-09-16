"""Controlled flag vocabulary + governance helpers (architecture §3.4/§3.7).

The taxonomy itself is versioned in ``config/vocabulary.json`` (``vocab_v1.0``);
this module only interprets it. ``STRENGTH_SCORE`` is the locked §3.7 numeric
mapping and lives in code, mirroring the architecture's literal constant.
"""

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
    """Load the versioned controlled vocabulary (``config/vocabulary.json``)."""
    return load_vocabulary()


def get_hierarchy_rank(
    flag_type: FlagType | str,
    vocabulary: VocabularyConfig | None = None,
) -> int:
    """Return the hierarchy rank for ``flag_type`` (§3.6).

    Rank 1 is the highest priority (``cancellation_intent``); ``positive_feedback``
    and ``other`` are non-priority and return ``NON_PRIORITY_RANK`` (weakest). A
    lower number therefore always means "wins".
    """
    vocab = vocabulary or get_vocabulary()
    key = flag_type if isinstance(flag_type, FlagType) else FlagType(flag_type)
    rank = vocab.ranks.get(key)
    return NON_PRIORITY_RANK if rank is None else rank


def check_vocabulary_governance(
    flags: Sequence[RiskFlag | AggregatedRiskFlag],
    vocabulary: VocabularyConfig | None = None,
) -> list[str]:
    """Return governance warnings for a flag population (§3.4).

    Flags the ``other`` bucket when it exceeds the configured share of all
    flags; ``other`` should be reviewed and either promoted (new vocabulary
    version) or confirmed residual. High-priority flags are never silently
    moved into ``other`` — that is enforced upstream in the extractor.
    """
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

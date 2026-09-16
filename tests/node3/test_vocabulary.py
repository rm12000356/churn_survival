from __future__ import annotations

from datetime import UTC, datetime

from config.models import VocabularyConfig
from node3.vocabulary import (
    NON_PRIORITY_RANK,
    STRENGTH_SCORE,
    check_vocabulary_governance,
    get_hierarchy_rank,
)
from schemas.enums import FlagType, Severity, SignalStrength
from schemas.node3 import Evidence, RiskFlag


def _flag(flag_type: FlagType) -> RiskFlag:
    return RiskFlag(
        flag_type=flag_type,
        severity=Severity.LOW,
        signal_strength=SignalStrength.WEAK,
        confidence=0.5,
        evidence=Evidence(
            message_id="m1", text="hello", timestamp=datetime(2026, 8, 1, tzinfo=UTC)
        ),
    )


def test_hierarchy_ranks(vocabulary: VocabularyConfig) -> None:
    assert get_hierarchy_rank(FlagType.CANCELLATION_INTENT, vocabulary) == 1
    assert get_hierarchy_rank("renewal_or_contract_concern", vocabulary) == 2
    assert get_hierarchy_rank(FlagType.PRODUCT_BUG_OR_OUTAGE, vocabulary) == 3
    assert get_hierarchy_rank(FlagType.COMPETITOR_MENTION, vocabulary) == 5
    assert get_hierarchy_rank(FlagType.OTHER, vocabulary) == NON_PRIORITY_RANK
    assert get_hierarchy_rank(FlagType.POSITIVE_FEEDBACK, vocabulary) == NON_PRIORITY_RANK


def test_strength_score_locked() -> None:
    assert STRENGTH_SCORE == {
        SignalStrength.WEAK: 1,
        SignalStrength.MODERATE: 2,
        SignalStrength.STRONG: 3,
    }


def test_governance_warns_when_other_exceeds_threshold(
    vocabulary: VocabularyConfig,
) -> None:
    flags = [_flag(FlagType.OTHER) for _ in range(1)] + [
        _flag(FlagType.BILLING_COMPLAINT) for _ in range(1)
    ]
    warnings = check_vocabulary_governance(flags, vocabulary)
    assert warnings and "other" in warnings[0]


def test_governance_quiet_below_threshold(vocabulary: VocabularyConfig) -> None:
    flags = [_flag(FlagType.OTHER)] + [
        _flag(FlagType.BILLING_COMPLAINT) for _ in range(9)
    ]
    assert check_vocabulary_governance(flags, vocabulary) == []


def test_governance_empty_flags(vocabulary: VocabularyConfig) -> None:
    assert check_vocabulary_governance([], vocabulary) == []

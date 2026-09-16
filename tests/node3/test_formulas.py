from __future__ import annotations

from datetime import UTC, datetime

from config.models import Node3Config
from node3.aggregate import (
    adjusted_strength,
    compute_evidence_quality_score,
    compute_overall_signal_confidence,
)
from schemas.enums import FlagType, Severity, SignalStrength, SupportDataStatus
from schemas.node3 import Evidence, RiskFlag


def _flag(text: str, strength: SignalStrength = SignalStrength.STRONG) -> RiskFlag:
    return RiskFlag(
        flag_type=FlagType.CANCELLATION_INTENT,
        severity=Severity.HIGH,
        signal_strength=strength,
        confidence=0.9,
        evidence=Evidence(
            message_id="m1", text=text, timestamp=datetime(2026, 8, 1, tzinfo=UTC)
        ),
    )


def test_evidence_quality_empty() -> None:
    assert compute_evidence_quality_score([]) == 0.0


def test_evidence_quality_length_only() -> None:
    text = "one two three four five six seven eight nine ten eleven twelve"
    assert compute_evidence_quality_score([_flag(text)]) == 0.75


def test_evidence_quality_clarity_bonus_capped() -> None:
    text = "I want to cancel one two three four five six seven eight nine ten eleven"
    assert compute_evidence_quality_score([_flag(text)]) == 1.0


def test_evidence_quality_short_text() -> None:
    assert compute_evidence_quality_score([_flag("cancel my plan please")]) == 0.5


def test_evidence_quality_average() -> None:
    long = "one two three four five six seven eight nine ten eleven twelve"
    short = "cancel my plan please"
    assert compute_evidence_quality_score([_flag(long), _flag(short)]) == round(
        (0.75 + 0.5) / 2, 3
    )


def test_overall_confidence_no_data_zero() -> None:
    assert (
        compute_overall_signal_confidence(
            SupportDataStatus.NO_DATA, 0, 0, 0.0, 0.0, 0.0
        )
        == 0.0
    )


def test_overall_confidence_max() -> None:
    value = compute_overall_signal_confidence(
        SupportDataStatus.SUFFICIENT_DATA, 8, 3, 1.0, 1.0, 1.0
    )
    assert value == 1.0


def test_overall_confidence_midpoint() -> None:
    value = compute_overall_signal_confidence(
        SupportDataStatus.LIMITED_DATA, 2, 1, 0.5, 1.0, 0.5
    )
    assert value == 0.542


def test_adjusted_strength_default_lambda(node3_config: Node3Config) -> None:
    flag = _flag("cancel now", SignalStrength.STRONG)
    assert adjusted_strength(flag, 0, node3_config) == 3.0


def test_adjusted_strength_persistent_lambda(node3_config: Node3Config) -> None:
    default_flag = _flag("billing issue", SignalStrength.STRONG).model_copy(
        update={"flag_type": FlagType.BILLING_COMPLAINT}
    )
    renewal_flag = _flag("renew now", SignalStrength.STRONG).model_copy(
        update={"flag_type": FlagType.RENEWAL_OR_CONTRACT_CONCERN}
    )
    default = adjusted_strength(default_flag, 100, node3_config)
    persistent = adjusted_strength(renewal_flag, 100, node3_config)
    assert persistent > default

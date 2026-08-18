from __future__ import annotations

from schemas.enums import (
    EvidenceMode,
    FlagType,
    ModelStatus,
    OverallSignalStrength,
    ReasonType,
    ReportRiskLevel,
    RiskLevel,
    Severity,
    SignalStrength,
)


def test_model_status_values() -> None:
    assert {s.value for s in ModelStatus} == {
        "READY",
        "WARNING",
        "FALLBACK",
        "INSUFFICIENT_DATA",
        "FAILED",
    }


def test_flag_type_vocabulary() -> None:
    assert FlagType.CANCELLATION_INTENT.value == "cancellation_intent"
    assert FlagType.POSITIVE_FEEDBACK.value == "positive_feedback"
    assert FlagType.OTHER.value == "other"
    assert len(set(FlagType)) == 10


def test_signal_strength_subset() -> None:
    assert {s.value for s in SignalStrength} == {"weak", "moderate", "strong"}
    assert {s.value for s in OverallSignalStrength} == {
        "none",
        "weak",
        "moderate",
        "strong",
    }


def test_severity_has_no_critical() -> None:
    assert {s.value for s in Severity} == {"low", "medium", "high"}


def test_risk_levels() -> None:
    assert {r.value for r in RiskLevel} == {
        "critical",
        "high",
        "medium",
        "low",
        "insufficient_data",
    }
    assert {r.value for r in ReportRiskLevel} == {"critical", "high", "medium", "low"}


def test_reason_type_includes_critical_rules() -> None:
    critical = {
        ReasonType.CRITICAL_CANCELLATION_INTENT,
        ReasonType.CRITICAL_CANCELLATION_PLUS_SIGNIFICANT_FLAG,
        ReasonType.CRITICAL_HIGH_QUANT_PLUS_CONTRACT_CONCERN,
        ReasonType.CRITICAL_REPEATED_HIGH_SEVERITY_PLUS_HIGH_QUANT,
    }
    assert critical <= set(ReasonType)


def test_evidence_mode_default_is_short_quote() -> None:
    assert EvidenceMode.SHORT_QUOTE.value == "short_quote"


def test_str_enum_serializes_to_exact_string() -> None:
    assert str(ModelStatus.WARNING) == "WARNING"
    assert str(RiskLevel.INSUFFICIENT_DATA) == "insufficient_data"
    assert str(FlagType.CANCELLATION_INTENT) == "cancellation_intent"

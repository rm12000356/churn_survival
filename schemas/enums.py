"""Centralized, versioned enums (ROADMAP Task 1.2).

Every enumerated vocabulary used by the pipeline contracts lives here as a
`StrEnum`, so members serialize to the exact strings the architecture uses.
Vocabulary is versioned (e.g. ``vocabulary_version``): adding a ``flag_type``
requires a version bump, never a silent edit (architecture §3.13).
"""

from __future__ import annotations

from enum import StrEnum


class ModelStatus(StrEnum):
    """Node 2 model status (architecture §2.3)."""

    READY = "READY"
    WARNING = "WARNING"
    FALLBACK = "FALLBACK"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    FAILED = "FAILED"


class ModelType(StrEnum):
    """Node 2 model type (architecture §2.12)."""

    COX_PH = "cox_ph"
    KAPLAN_MEIER = "kaplan_meier"
    NONE = "none"


class HorizonStatus(StrEnum):
    """Survival horizon availability (architecture §2.7/§2.12)."""

    AVAILABLE = "AVAILABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class CustomerState(StrEnum):
    """Per-customer Node 2 state (architecture §2.12)."""

    SCORED = "scored"
    NOT_ENOUGH_DATA = "not_enough_data"
    EXCLUDED = "excluded"


class FlagType(StrEnum):
    """Controlled vocabulary for support risk flags (architecture §3.4).

    Versioned. ``other`` is the residual bucket; ``positive_feedback`` is
    contextual only and never reduces risk.
    """

    CANCELLATION_INTENT = "cancellation_intent"
    RENEWAL_OR_CONTRACT_CONCERN = "renewal_or_contract_concern"
    PRODUCT_BUG_OR_OUTAGE = "product_bug_or_outage"
    POOR_SUPPORT_EXPERIENCE = "poor_support_experience"
    BILLING_COMPLAINT = "billing_complaint"
    FEATURE_MISSING = "feature_missing"
    USAGE_DROP_RELATED = "usage_drop_related"
    COMPETITOR_MENTION = "competitor_mention"
    POSITIVE_FEEDBACK = "positive_feedback"
    OTHER = "other"


class SignalStrength(StrEnum):
    """Thread-level flag strength (architecture §3.4)."""

    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class OverallSignalStrength(StrEnum):
    """Customer-level signal strength, including the no-signal state (architecture §3.5)."""

    NONE = "none"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class Severity(StrEnum):
    """Risk-flag severity (architecture §3.4)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReportSeverity(StrEnum):
    """Report-reason severity (architecture §5.10), adds ``critical``."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskLevel(StrEnum):
    """Node 4 combined risk level (architecture §4.21)."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT_DATA = "insufficient_data"


class ReportRiskLevel(StrEnum):
    """Node 5 customer-report risk level (architecture §5.9) — no insufficient_data."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ReasonType(StrEnum):
    """Deterministic primary-reason types (architecture §4.11, §4.16)."""

    CRITICAL_CANCELLATION_INTENT = "critical_cancellation_intent"
    CRITICAL_CANCELLATION_PLUS_SIGNIFICANT_FLAG = "critical_cancellation_plus_significant_flag"
    CRITICAL_HIGH_QUANT_PLUS_CONTRACT_CONCERN = "critical_high_quant_plus_contract_concern"
    CRITICAL_REPEATED_HIGH_SEVERITY_PLUS_HIGH_QUANT = (
        "critical_repeated_high_severity_plus_high_quant"
    )
    HIGH_QUANTITATIVE_RISK = "high_quantitative_risk"
    MODERATE_QUANTITATIVE_RISK = "moderate_quantitative_risk"
    STRONG_SUPPORT_SIGNAL = "strong_support_signal"
    MODERATE_SUPPORT_SIGNAL = "moderate_support_signal"
    REPEATED_SUPPORT_ISSUE = "repeated_support_issue"
    HIGH_SUPPORT_URGENCY = "high_support_urgency"
    LIMITED_SUPPORT_DATA = "limited_support_data"
    MISSING_SUPPORT_DATA = "missing_support_data"
    MISSING_QUANTITATIVE_DATA = "missing_quantitative_data"
    QUANTITATIVE_QUALITATIVE_AGREEMENT = "quantitative_qualitative_agreement"
    QUANTITATIVE_QUALITATIVE_CONFLICT = "quantitative_qualitative_conflict"


class EvidenceMode(StrEnum):
    """Client-facing evidence presentation mode (architecture §5.13)."""

    DISABLED = "disabled"
    SUMMARY_ONLY = "summary_only"
    SHORT_QUOTE = "short_quote"
    FULL_EVIDENCE = "full_evidence"


class LanguageStatus(StrEnum):
    """Per-thread language status (architecture §3.4)."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class SentimentLabel(StrEnum):
    """Sentiment label (architecture §3.4/§3.5)."""

    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class UrgencyLevel(StrEnum):
    """Thread/customer urgency (architecture §3.4/§3.5)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class SupportDataStatus(StrEnum):
    """Customer-level support data availability (architecture §3.5)."""

    NO_DATA = "no_data"
    LIMITED_DATA = "limited_data"
    SUFFICIENT_DATA = "sufficient_data"


class ValidationStatus(StrEnum):
    """Node 1 validation report status (architecture §1.2)."""

    PASSED = "PASSED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"

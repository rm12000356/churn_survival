from __future__ import annotations

from enum import StrEnum


class ModelStatus(StrEnum):
    READY = "READY"
    WARNING = "WARNING"
    FALLBACK = "FALLBACK"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    FAILED = "FAILED"


class ModelType(StrEnum):
    COX_PH = "cox_ph"
    KAPLAN_MEIER = "kaplan_meier"
    NONE = "none"


class HorizonStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class CustomerState(StrEnum):
    SCORED = "scored"
    NOT_ENOUGH_DATA = "not_enough_data"
    EXCLUDED = "excluded"


class FlagType(StrEnum):
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
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class OverallSignalStrength(StrEnum):
    NONE = "none"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReportSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskLevel(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT_DATA = "insufficient_data"


class ReportRiskLevel(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT_DATA = "insufficient_data"


class ReasonType(StrEnum):
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
    DISABLED = "disabled"
    SUMMARY_ONLY = "summary_only"
    SHORT_QUOTE = "short_quote"
    FULL_EVIDENCE = "full_evidence"


class LanguageStatus(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class SentimentLabel(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class UrgencyLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class SupportDataStatus(StrEnum):
    NO_DATA = "no_data"
    LIMITED_DATA = "limited_data"
    SUFFICIENT_DATA = "sufficient_data"


class ValidationStatus(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"

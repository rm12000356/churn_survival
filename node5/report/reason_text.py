from __future__ import annotations

from schemas.enums import ReasonType

REASON_STATEMENTS: dict[ReasonType, str] = {
    ReasonType.CRITICAL_CANCELLATION_INTENT: (
        "Explicit cancellation intent was detected in recent support interactions."
    ),
    ReasonType.CRITICAL_CANCELLATION_PLUS_SIGNIFICANT_FLAG: (
        "Strong cancellation intent was detected alongside another significant risk signal."
    ),
    ReasonType.CRITICAL_HIGH_QUANT_PLUS_CONTRACT_CONCERN: (
        "Very high quantitative risk coincides with a strong renewal or contract concern."
    ),
    ReasonType.CRITICAL_REPEATED_HIGH_SEVERITY_PLUS_HIGH_QUANT: (
        "Repeated high-severity support problems coincide with very high quantitative risk."
    ),
    ReasonType.HIGH_QUANTITATIVE_RISK: (
        "The quantitative survival model indicates high churn risk."
    ),
    ReasonType.MODERATE_QUANTITATIVE_RISK: (
        "The quantitative survival model indicates moderate churn risk."
    ),
    ReasonType.STRONG_SUPPORT_SIGNAL: "A strong qualitative support signal was detected.",
    ReasonType.MODERATE_SUPPORT_SIGNAL: "A moderate qualitative support signal was detected.",
    ReasonType.REPEATED_SUPPORT_ISSUE: (
        "The customer raised the same support issue repeatedly."
    ),
    ReasonType.HIGH_SUPPORT_URGENCY: "Support interactions indicated high urgency.",
    ReasonType.LIMITED_SUPPORT_DATA: "Only limited support data was available.",
    ReasonType.MISSING_SUPPORT_DATA: "No support data was available.",
    ReasonType.MISSING_QUANTITATIVE_DATA: "No quantitative risk score was available.",
    ReasonType.QUANTITATIVE_QUALITATIVE_AGREEMENT: (
        "Quantitative and qualitative signals agree."
    ),
    ReasonType.QUANTITATIVE_QUALITATIVE_CONFLICT: (
        "Quantitative and qualitative signals diverge."
    ),
}


HEADLINE_PHRASES: dict[ReasonType, str] = {
    ReasonType.CRITICAL_CANCELLATION_INTENT: "cancellation intent detected",
    ReasonType.CRITICAL_CANCELLATION_PLUS_SIGNIFICANT_FLAG: (
        "cancellation intent plus another significant risk signal"
    ),
    ReasonType.CRITICAL_HIGH_QUANT_PLUS_CONTRACT_CONCERN: (
        "high quantitative risk with a contract concern"
    ),
    ReasonType.CRITICAL_REPEATED_HIGH_SEVERITY_PLUS_HIGH_QUANT: (
        "repeated high-severity issues with high quantitative risk"
    ),
    ReasonType.HIGH_QUANTITATIVE_RISK: "high quantitative risk",
    ReasonType.MODERATE_QUANTITATIVE_RISK: "moderate quantitative risk",
    ReasonType.STRONG_SUPPORT_SIGNAL: "strong support signal",
    ReasonType.MODERATE_SUPPORT_SIGNAL: "moderate support signal",
    ReasonType.REPEATED_SUPPORT_ISSUE: "repeated support issues",
    ReasonType.HIGH_SUPPORT_URGENCY: "high support urgency",
    ReasonType.LIMITED_SUPPORT_DATA: "limited support data",
    ReasonType.MISSING_SUPPORT_DATA: "no support data",
    ReasonType.MISSING_QUANTITATIVE_DATA: "no quantitative risk score",
    ReasonType.QUANTITATIVE_QUALITATIVE_AGREEMENT: "quantitative and qualitative agreement",
    ReasonType.QUANTITATIVE_QUALITATIVE_CONFLICT: (
        "divergence between quantitative and qualitative signals"
    ),
}


def reason_statement(reason_type: ReasonType) -> str:
    return REASON_STATEMENTS[reason_type]


def headline_phrase(reason_type: ReasonType) -> str:
    return HEADLINE_PHRASES[reason_type]

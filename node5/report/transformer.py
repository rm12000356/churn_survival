"""Node 4 account -> client `CustomerReport` transformation (architecture §5.8–§5.14).

Presentation only. Every decision field (risk level, score, confidence, rank) is
copied verbatim from Node 4; nothing is recalculated or re-sorted.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from node5.report.reason_text import headline_phrase, reason_statement
from schemas.enums import (
    FlagType,
    ReportRiskLevel,
    ReportSeverity,
    Severity,
    SignalStrength,
    SupportDataStatus,
)
from schemas.node4 import RankedAccount
from schemas.node5 import (
    CustomerReport,
    QuantitativeSummary,
    ReportEvidence,
    ReportFlag,
    ReportReason,
    SupportSummary,
)

_LOW_CONFIDENCE = 0.5
# F-8: bound client-facing display names (never affects decisions).
_DISPLAY_NAME_MAX = 120
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_WHITESPACE = re.compile(r"\s+")


class CustomerProfile(BaseModel):
    """Optional, presentation-only customer context (architecture §5.3)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    account_name: str | None = None
    segment: str | None = None
    industry: str | None = None
    owner: str | None = None


CustomerData = Mapping[str, CustomerProfile]


def coerce_customer_data(raw: Any) -> dict[str, CustomerProfile]:
    """Validate optional customer context; context never affects risk (D-INSUF)."""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("customer_data must be a mapping keyed by customer_id")
    result: dict[str, CustomerProfile] = {}
    for customer_id, value in raw.items():
        if isinstance(value, CustomerProfile):
            result[str(customer_id)] = value
        else:
            result[str(customer_id)] = CustomerProfile.model_validate(value)
    return result


def sanitize_display_name(name: str) -> str:
    """Bound/sanitize a client-facing name (F-8): drop control chars, collapse
    whitespace, cap length. Never destructive to legitimate names."""
    cleaned = _CONTROL_CHARS.sub(" ", name)
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    if len(cleaned) > _DISPLAY_NAME_MAX:
        cleaned = cleaned[:_DISPLAY_NAME_MAX].strip()
    return cleaned


def display_name_for(customer_id: str, customer_data: Mapping[str, CustomerProfile]) -> str:
    profile = customer_data.get(customer_id)
    if profile is None:
        return customer_id
    raw = profile.name or profile.account_name or ""
    cleaned = sanitize_display_name(raw)
    return cleaned or customer_id


def map_reasons(account: RankedAccount) -> list[ReportReason]:
    """Map structured reasons to client reasons, preserving order (§5.10)."""
    reasons: list[ReportReason] = []
    for reason in account.primary_reasons:
        reasons.append(
            ReportReason(
                reason_type=reason.reason_type,
                source=reason.source,
                severity=ReportSeverity(reason.severity),
                statement=reason_statement(reason.reason_type),
                evidence_ref=reason.evidence_ref,
            )
        )
    return reasons


def map_quantitative(account: RankedAccount) -> QuantitativeSummary:
    quantitative = account.quantitative
    return QuantitativeSummary(
        risk_score=quantitative.risk_score,
        survival_prob_90d=quantitative.survival_prob_90d,
        top_drivers=list(quantitative.top_drivers),
        customer_state=quantitative.customer_state,
    )


def map_support(
    account: RankedAccount, errors: list[dict[str, Any]] | None = None
) -> SupportSummary:
    """Map support signals; malformed top_flags are surfaced, never dropped (F-4)."""
    errors = errors if errors is not None else []
    qualitative = account.qualitative
    flags: list[ReportFlag] = []
    for index, entry in enumerate(qualitative.top_flags):
        if not isinstance(entry, dict):
            errors.append(_top_flag_error(account, index, "entry is not a mapping"))
            continue
        try:
            flags.append(
                ReportFlag(
                    flag_type=FlagType(entry["flag_type"]),
                    severity=Severity(entry["severity"]),
                    signal_strength=SignalStrength(entry["signal_strength"]),
                )
            )
        except (KeyError, ValueError, TypeError) as exc:
            errors.append(_top_flag_error(account, index, str(exc)))
    return SupportSummary(
        support_data_status=qualitative.support_data_status,
        signal_strength=qualitative.signal_strength,
        churn_language_detected=qualitative.churn_language_detected,
        escalation_signal=qualitative.escalation_signal,
        top_flags=flags,
    )


def _top_flag_error(account: RankedAccount, index: int, detail: str) -> dict[str, Any]:
    return {
        "code": "INVALID_TOP_FLAG",
        "customer_id": account.customer_id,
        "source": "node4",
        "message": f"malformed top_flags[{index}] omitted: {detail}",
    }


def account_quality_notes(account: RankedAccount) -> list[str]:
    notes: list[str] = []
    if account.qualitative.support_data_status == SupportDataStatus.NO_DATA:
        notes.append("No support data was available.")
    elif account.qualitative.support_data_status == SupportDataStatus.LIMITED_DATA:
        notes.append("Only limited support data was available.")
    if account.combined_confidence < _LOW_CONFIDENCE:
        notes.append("The risk classification is supported by limited available data.")
    return notes


def build_template_explanation(
    account: RankedAccount, display_name: str
) -> tuple[str, str]:
    """Deterministic headline/summary used when the LLM is off or fails (§5.14)."""
    level_label = ReportRiskLevel(account.combined_risk_level.value).value.capitalize()
    if account.primary_reasons:
        headline = f"{level_label} — {headline_phrase(account.primary_reasons[0].reason_type)}"
    else:
        headline = f"{level_label} risk"

    parts = [f"{display_name} is classified as {level_label} risk."]
    if account.primary_reasons:
        parts.append(reason_statement(account.primary_reasons[0].reason_type))
    quantitative = account.quantitative
    if quantitative.survival_prob_90d is not None:
        parts.append(
            f"The model estimates a 90-day survival probability of "
            f"{quantitative.survival_prob_90d:.3f}."
        )
    elif quantitative.risk_score is not None:
        parts.append(f"The model's risk score is {quantitative.risk_score:.3f}.")
    if account.qualitative.support_data_status == SupportDataStatus.NO_DATA:
        parts.append("No support data was available.")
    elif account.qualitative.top_flags:
        flag_types = [
            str(entry.get("flag_type"))
            for entry in account.qualitative.top_flags
            if isinstance(entry, dict) and entry.get("flag_type")
        ]
        joined = ", ".join(flag.replace("_", " ") for flag in flag_types)
        parts.append(f"Support signals include {joined}.")
    return headline, " ".join(parts[:4])


def build_customer_report(
    account: RankedAccount,
    *,
    display_name: str,
    recommended_action: str | None,
    evidence: list[ReportEvidence],
    errors: list[dict[str, Any]] | None = None,
    headline: str | None = None,
    summary: str | None = None,
) -> CustomerReport:
    """Assemble one client report entry with copied decision fields (§5.9)."""
    template_headline, template_summary = build_template_explanation(account, display_name)
    explanation_source: Literal["llm", "template"] = (
        "llm" if headline is not None and summary is not None else "template"
    )
    return CustomerReport(
        customer_id=account.customer_id,
        display_name=display_name,
        rank=account.rank,
        risk_level=ReportRiskLevel(account.combined_risk_level.value),
        combined_score=account.combined_score,
        combined_confidence=account.combined_confidence,
        headline=headline if headline is not None else template_headline,
        summary=summary if summary is not None else template_summary,
        primary_reasons=map_reasons(account),
        quantitative_summary=map_quantitative(account),
        support_summary=map_support(account, errors),
        evidence=evidence,
        data_quality_notes=account_quality_notes(account),
        recommended_action=recommended_action,
        explanation_source=explanation_source,
    )

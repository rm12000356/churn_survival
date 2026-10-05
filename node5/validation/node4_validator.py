from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from schemas.enums import ReportSeverity, RiskLevel
from schemas.node4 import Node4Output, RankedAccount


@dataclass
class ValidationResult:
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


class Node5ValidationError(RuntimeError):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__(
            f"Node 4 output failed Node 5 validation with {len(errors)} error(s): "
            + "; ".join(str(error.get("code")) for error in errors[:5])
        )


def _error(code: str, message: str, customer_id: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if customer_id is not None:
        payload["customer_id"] = customer_id
    return payload


def validate_customer(account: RankedAccount, result: ValidationResult) -> None:
    if not account.customer_id or not account.customer_id.strip():
        result.errors.append(_error("INVALID_CUSTOMER_ID", "customer_id is empty"))
    if not (0.0 <= account.combined_score <= 1.0):
        result.errors.append(
            _error(
                "SCORE_OUT_OF_RANGE",
                f"combined_score {account.combined_score} not in [0, 1]",
                account.customer_id,
            )
        )
    if not (0.0 <= account.combined_confidence <= 1.0):
        result.errors.append(
            _error(
                "CONFIDENCE_OUT_OF_RANGE",
                f"combined_confidence {account.combined_confidence} not in [0, 1]",
                account.customer_id,
            )
        )
    if account.combined_risk_level == RiskLevel.CRITICAL and not any(
        reason.reason_type.value.startswith("critical_") for reason in account.primary_reasons
    ):
        result.errors.append(
            _error(
                "CRITICAL_WITHOUT_REASON",
                "critical account has no qualifying critical reason",
                account.customer_id,
            )
        )
    valid_severities = {member.value for member in ReportSeverity}
    for reason in account.primary_reasons:
        if reason.severity not in valid_severities:
            result.errors.append(
                _error(
                    "INVALID_REASON_SEVERITY",
                    f"reason severity {reason.severity!r} is not a report severity",
                    account.customer_id,
                )
            )


def validate_evidence_refs(account: RankedAccount, result: ValidationResult) -> None:
    refs = account.evidence_refs
    code = "INVALID_EVIDENCE_REFS"
    cid = account.customer_id
    if not isinstance(refs.node2.model_version, str):
        result.errors.append(_error(code, "node2 model_version is not a string", cid))
    if not isinstance(refs.node3.signal_version, str):
        result.errors.append(_error(code, "node3 signal_version is not a string", cid))
    if not all(isinstance(item, str) for item in refs.node3.message_ids):
        result.errors.append(_error(code, "node3 message_ids must be strings", cid))


def validate_summary_stats(output: Node4Output, result: ValidationResult) -> None:
    stats = output.summary_stats
    main = output.ranked_accounts
    insufficient = output.insufficient_data_accounts
    actual = {
        "n_customers": len(main) + len(insufficient),
        "n_critical": sum(1 for a in main if a.combined_risk_level == RiskLevel.CRITICAL),
        "n_high": sum(1 for a in main if a.combined_risk_level == RiskLevel.HIGH),
        "n_medium": sum(1 for a in main if a.combined_risk_level == RiskLevel.MEDIUM),
        "n_low": sum(1 for a in main if a.combined_risk_level == RiskLevel.LOW),
        "n_insufficient_data": len(insufficient),
        "n_churned": len(output.churned_accounts),
    }
    for key, value in actual.items():
        if getattr(stats, key) != value:
            result.errors.append(
                _error(
                    "SUMMARY_STATS_MISMATCH",
                    f"{key} declared {getattr(stats, key)} but computed {value}",
                )
            )


def validate_ranking(output: Node4Output, result: ValidationResult) -> None:
    ranks = [account.rank for account in output.ranked_accounts]
    if ranks != list(range(1, len(ranks) + 1)):
        result.errors.append(
            _error("RANKS_NOT_SEQUENTIAL", f"main-list ranks are not 1..{len(ranks)}: {ranks}")
        )
    main_ids = [account.customer_id for account in output.ranked_accounts]
    if len(main_ids) != len(set(main_ids)):
        duplicates = sorted({cid for cid in main_ids if main_ids.count(cid) > 1})
        result.errors.append(
            _error("DUPLICATE_CUSTOMER", f"duplicate customer_id(s) in main list: {duplicates}")
        )
    insufficient_list = [
        account.customer_id for account in output.insufficient_data_accounts
    ]
    if len(insufficient_list) != len(set(insufficient_list)):
        duplicates = sorted(
            {cid for cid in insufficient_list if insufficient_list.count(cid) > 1}
        )
        result.errors.append(
            _error(
                "DUPLICATE_CUSTOMER",
                f"duplicate customer_id(s) in insufficient-data list: {duplicates}",
            )
        )
    insufficient_ids = set(insufficient_list)
    overlap = sorted(set(main_ids) & insufficient_ids)
    if overlap:
        result.errors.append(
            _error("CROSS_LIST_MEMBERSHIP", f"customer(s) in both lists: {overlap}")
        )
    churned_list = [account.customer_id for account in output.churned_accounts]
    if len(churned_list) != len(set(churned_list)):
        duplicates = sorted({cid for cid in churned_list if churned_list.count(cid) > 1})
        result.errors.append(
            _error(
                "DUPLICATE_CUSTOMER",
                f"duplicate customer_id(s) in churned list: {duplicates}",
            )
        )
    churned_overlap = sorted(set(churned_list) & (set(main_ids) | insufficient_ids))
    if churned_overlap:
        result.errors.append(
            _error(
                "CROSS_LIST_MEMBERSHIP",
                f"churned customer(s) also ranked or insufficient: {churned_overlap}",
            )
        )


def validate_node4_output(
    output: Node4Output,
    *,
    expected_reference_date: date | None = None,
) -> ValidationResult:
    result = ValidationResult()
    for account in [*output.ranked_accounts, *output.insufficient_data_accounts]:
        validate_customer(account, result)
        validate_evidence_refs(account, result)
    validate_summary_stats(output, result)
    validate_ranking(output, result)
    if expected_reference_date is not None and output.reference_date != expected_reference_date:
        result.errors.append(
            _error(
                "REFERENCE_DATE_MISMATCH",
                f"Node 4 reference_date {output.reference_date} != config "
                f"{expected_reference_date}",
            )
        )
    return result

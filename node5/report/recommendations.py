from __future__ import annotations

from config.models import ActionRulesConfig
from schemas.enums import FlagType
from schemas.node4 import RankedAccount

RECOMMENDATION_PRIORITY: tuple[FlagType, ...] = (
    FlagType.CANCELLATION_INTENT,
    FlagType.RENEWAL_OR_CONTRACT_CONCERN,
    FlagType.PRODUCT_BUG_OR_OUTAGE,
    FlagType.POOR_SUPPORT_EXPERIENCE,
    FlagType.BILLING_COMPLAINT,
    FlagType.USAGE_DROP_RELATED,
    FlagType.FEATURE_MISSING,
    FlagType.COMPETITOR_MENTION,
    FlagType.OTHER,
)


def _candidate_flag_types(account: RankedAccount) -> list[FlagType]:
    seen: list[FlagType] = []

    def _add(raw: object) -> None:
        try:
            flag = FlagType(str(raw))
        except ValueError:
            return
        if flag == FlagType.POSITIVE_FEEDBACK:
            return
        if flag not in seen:
            seen.append(flag)

    for entry in account.qualitative.top_flags:
        if isinstance(entry, dict):
            _add(entry.get("flag_type"))
    for reason in account.primary_reasons:
        ref = reason.evidence_ref
        if isinstance(ref, dict):
            _add(ref.get("flag_type"))
    return seen


def select_recommendation(
    account: RankedAccount,
    action_rules: ActionRulesConfig | None,
    *,
    enabled: bool,
) -> tuple[str | None, FlagType | None]:
    if not enabled or action_rules is None:
        return None, None
    available = set(_candidate_flag_types(account))
    for flag_type in RECOMMENDATION_PRIORITY:
        if flag_type in available:
            rule = action_rules.rules.get(flag_type.value)
            if rule is not None:
                return rule, flag_type
    return None, None

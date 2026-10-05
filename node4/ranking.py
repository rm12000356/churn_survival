from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from config.models import Node4Config
from schemas.enums import RiskLevel
from schemas.node4 import RankedAccount

_RISK_ORDER: dict[RiskLevel, int] = {
    RiskLevel.CRITICAL: 0,
    RiskLevel.HIGH: 1,
    RiskLevel.MEDIUM: 2,
    RiskLevel.LOW: 3,
}


def sort_key(account: RankedAccount, config: Node4Config) -> tuple[Any, ...]:
    quantitative_risk = account.quantitative.normalized_risk
    return (
        _RISK_ORDER[account.combined_risk_level],
        -account.combined_score,
        0 if account.qualitative.churn_language_detected else 1,
        -config.strength_order[account.qualitative.signal_strength],
        (0, -quantitative_risk) if quantitative_risk is not None else (1, 0.0),
        -account.combined_confidence,
        account.customer_id,
    )


def rank_accounts(
    accounts: Sequence[RankedAccount],
    config: Node4Config,
) -> list[RankedAccount]:
    ordered = sorted(accounts, key=lambda account: sort_key(account, config))
    return [account.model_copy(update={"rank": index}) for index, account in enumerate(ordered, 1)]


def sort_insufficient_data_accounts(
    accounts: Sequence[RankedAccount],
) -> list[RankedAccount]:
    return sorted(accounts, key=lambda account: account.customer_id)

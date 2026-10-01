"""Node 4 v4 — per-account drivers from Node 2 contributions (§4.4b, 2026-10-01).

Drivers are explanation metadata only: positive contributions to relative
log-hazard, reliable-only under v4, sorted by contribution, capped. Decision
outputs (score, level, rank, confidence, summary stats) never move; v1–v3 keep
the model-wide D-2 list even when Node 2 carries contributions.
"""

from __future__ import annotations

from typing import Any

import pytest

from config.loader import load_node4_config
from config.models import Node4Config
from node4.node import run_node4
from node4.quantitative import driver_details_for_customer, top_drivers_for_customer
from schemas.enums import ReasonType
from schemas.node2 import FeatureContribution, Node2Output
from schemas.node4 import Node4Output, RankedAccount
from tests.node4.conftest import make_association, make_node2


def _c(
    feature: str,
    contribution: float,
    *,
    reliable: bool = True,
    kind: str = "numeric",
    value: float | str = 1.0,
    reference: float | str | None = 2.0,
) -> FeatureContribution:
    return FeatureContribution.model_validate(
        {
            "feature": feature,
            "column": feature if kind == "numeric" else f"{feature}_{value}",
            "kind": kind,
            "value": value,
            "reference": reference,
            "coefficient": 0.1,
            "hazard_ratio": 1.105,
            "contribution": contribution,
            "reliable": reliable,
        }
    )


def _config(version: str, **overrides: Any) -> Node4Config:
    base = load_node4_config(version).model_dump()
    base.update(overrides)
    return Node4Config.model_validate(base)


# --------------------------------------------------------------------------- #
# Selection policy
# --------------------------------------------------------------------------- #


def test_policy_positive_only_reliable_only_sorted_and_capped() -> None:
    config = _config("4", top_drivers_max=2)
    contributions = [
        _c("usage_frequency", 0.42),
        _c("support_tickets_90d", 0.90, reliable=False),
        _c("contract_length_months", -0.30),
        _c("plan_tier", 0.35, kind="categorical", value="starter", reference="enterprise"),
        _c("seats", 0.10),
    ]
    details = driver_details_for_customer(contributions, config)
    assert [d.feature for d in details] == ["usage_frequency", "plan_tier"]
    assert all(d.reliable for d in details)
    assert details[1].value == "starter" and details[1].reference == "enterprise"
    assert top_drivers_for_customer(contributions, config) == ["usage_frequency", "plan_tier"]


def test_policy_without_reliability_filter_keeps_unreliable_with_provenance() -> None:
    config = _config("4", drivers_require_reliable=False)
    details = driver_details_for_customer([_c("a", 0.2), _c("b", 0.5, reliable=False)], config)
    assert [(d.feature, d.reliable) for d in details] == [("b", False), ("a", True)]


def test_policy_ties_break_on_feature_name_and_empty_input() -> None:
    config = _config("4")
    details = driver_details_for_customer([_c("zeta", 0.3), _c("alpha", 0.3)], config)
    assert [d.feature for d in details] == ["alpha", "zeta"]
    assert driver_details_for_customer(None, config) == []
    assert driver_details_for_customer([_c("a", 0.0), _c("b", -1.0)], config) == []


def test_v4_config_turns_both_flags_on_and_v3_off() -> None:
    v4 = load_node4_config("4")
    v3 = load_node4_config("3")
    assert v4.per_customer_drivers and v4.drivers_require_reliable
    assert not v3.per_customer_drivers and not v3.drivers_require_reliable
    # v4 differs from v3 only in the driver policy and its ranking version.
    flags = {"per_customer_drivers", "drivers_require_reliable"}
    a = v3.model_dump(exclude=flags)
    b = v4.model_dump(exclude=flags)
    assert a.pop("ranking_version") == "1.2" and b.pop("ranking_version") == "1.3"
    assert a == b


# --------------------------------------------------------------------------- #
# End-to-end through run_node4
# --------------------------------------------------------------------------- #

ASSOCIATIONS = [
    make_association("plan_tier_starter", coefficient=0.35, hazard_ratio=1.42),
    make_association("support_tickets_90d", coefficient=0.006, hazard_ratio=1.006),
]


def _node2(with_contributions: bool = True) -> Node2Output:
    node2 = make_node2(
        ["A", "B", "C", "D"],
        states=["scored", "excluded", "scored", "scored"],
        risk_scores=[0.6, 0.2, 0.4],
        survival_90=[0.4, 0.8, 0.6],
        feature_associations=[a.model_dump() for a in ASSOCIATIONS],
    )
    if not with_contributions:
        return node2
    return node2.model_copy(
        update={
            "baseline_log_hazard": -1.5,
            "customer_relative_log_hazard": [0.77, -0.6, 0.2],
            "customer_contributions": [
                [  # A: low usage + starter plan (+ an unreliable tickets term)
                    _c("usage_frequency", 0.42, value=3.0, reference=12.4),
                    _c("plan_tier", 0.35, kind="categorical", value="starter",
                       reference="enterprise"),
                    _c("support_tickets_90d", 0.01, reliable=False),
                ],
                [_c("usage_frequency", -0.6, value=30.0, reference=12.4)],  # C
                [_c("contract_length_months", 0.2, value=1.0, reference=14.0)],  # D
            ],
        }
    )


def _by_id(output: Node4Output) -> dict[str, RankedAccount]:
    return {
        a.customer_id: a for a in [*output.ranked_accounts, *output.insufficient_data_accounts]
    }


def test_each_account_gets_its_own_drivers_aligned_to_scored_subset() -> None:
    accounts = _by_id(run_node4(_node2(), None, load_node4_config("4")))
    a, b, c, d = (accounts[key] for key in "ABCD")
    assert a.quantitative.top_drivers == ["usage_frequency", "plan_tier"]
    assert [x.feature for x in a.quantitative.driver_details] == a.quantitative.top_drivers
    assert a.quantitative.relative_log_hazard == 0.77
    assert a.evidence_refs.node2.feature_refs == ["usage_frequency", "plan_tier"]
    assert c.quantitative.top_drivers == []
    assert d.quantitative.top_drivers == ["contract_length_months"]
    assert d.quantitative.relative_log_hazard == 0.2
    # Excluded customer: no scored slot, no drivers, never imputed.
    assert b.quantitative.top_drivers == []
    assert b.quantitative.driver_details == []
    assert b.quantitative.relative_log_hazard is None
    for account in accounts.values():
        assert "support_tickets_90d" not in account.quantitative.top_drivers


def test_quantitative_reason_carries_structured_driver_refs() -> None:
    account = _by_id(run_node4(_node2(), None, load_node4_config("4")))["A"]
    quant = [
        r
        for r in account.primary_reasons
        if r.source == "node2" and r.reason_type != ReasonType.MISSING_QUANTITATIVE_DATA
    ]
    assert quant
    for reason in quant:
        assert reason.evidence_ref["drivers"] == ["usage_frequency", "plan_tier"]


def test_legacy_fallback_when_node2_has_no_contributions() -> None:
    output = run_node4(_node2(with_contributions=False), None, load_node4_config("4"))
    assert output.ranked_accounts
    for account in output.ranked_accounts:
        assert account.quantitative.top_drivers == ["plan_tier_starter", "support_tickets_90d"]
        assert account.quantitative.driver_details == []
        assert account.quantitative.relative_log_hazard is None


@pytest.mark.parametrize("version", ["1", "2", "3"])
def test_older_configs_ignore_contributions_bit_identically(version: str) -> None:
    config = load_node4_config(version)
    with_c = run_node4(_node2(), None, config).model_dump_json()
    without = run_node4(_node2(with_contributions=False), None, config).model_dump_json()
    assert with_c == without


def _decisions(output: Node4Output) -> list[tuple[object, ...]]:
    return [
        (a.customer_id, a.combined_score, a.combined_risk_level, a.rank, a.combined_confidence)
        for a in [*output.ranked_accounts, *output.insufficient_data_accounts]
    ]


def test_v4_decision_outputs_equal_v3() -> None:
    v3 = run_node4(_node2(), None, load_node4_config("3"))
    v4 = run_node4(_node2(), None, load_node4_config("4"))
    assert _decisions(v3) == _decisions(v4)
    assert v3.summary_stats == v4.summary_stats
    assert [a.customer_id for a in v3.churned_accounts] == [
        a.customer_id for a in v4.churned_accounts
    ]

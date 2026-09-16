"""Multi-source Node 3 end-to-end + Node 4/Node 5 compatibility (addendum §15/§20/§21)."""

from __future__ import annotations

from config.loader import (
    load_action_rules,
    load_node3_config,
    load_node4_config,
    load_node5_config,
)
from config.models import IdentityMappingConfig, Node3SourcesConfig
from node3.node import run_node3_from_sources
from node4.node import run_node4
from node5.node import run_node5
from schemas.enums import FlagType, RiskLevel
from tests.node3.conftest import NOW
from tests.node4.conftest import make_node2

CUSTOMERS = ["CUST-A", "CUST-B", "CUST-C", "CUST-D", "CUST-E", "CUST-F", "CUST-G"]


def _run(sources_config: Node3SourcesConfig, identity_mapping: IdentityMappingConfig):
    return run_node3_from_sources(
        CUSTOMERS, load_node3_config("1"), sources_config, identity_mapping, now=NOW
    )


def test_multi_source_signals_aggregate(
    sources_config: Node3SourcesConfig, identity_mapping: IdentityMappingConfig
) -> None:
    output = _run(sources_config, identity_mapping)
    by_customer = {s.customer_id: s for s in output.customer_signals}

    assert set(by_customer) == set(CUSTOMERS)
    assert by_customer["CUST-E"].support_data_status.value == "no_data"

    # Customer A: positive feedback only, non-risk-bearing.
    a_types = {f.flag_type for f in by_customer["CUST-A"].risk_flags}
    assert a_types == {FlagType.POSITIVE_FEEDBACK}

    # Customer B: product complaint -> strong product bug/outage.
    b_flags = {f.flag_type: f for f in by_customer["CUST-B"].risk_flags}
    assert b_flags[FlagType.PRODUCT_BUG_OR_OUTAGE].signal_strength.value == "strong"

    # Customer C: competitor mention (moderate) + renewal concern from one Gmail thread.
    c_flags = {f.flag_type: f for f in by_customer["CUST-C"].risk_flags}
    assert c_flags[FlagType.COMPETITOR_MENTION].signal_strength.value == "moderate"
    assert FlagType.RENEWAL_OR_CONTRACT_CONCERN in c_flags

    # Customer D: strong cancellation intent across post + DM (recurrence 2).
    d_flags = {f.flag_type: f for f in by_customer["CUST-D"].risk_flags}
    assert d_flags[FlagType.CANCELLATION_INTENT].signal_strength.value == "strong"
    assert d_flags[FlagType.CANCELLATION_INTENT].recurrence_count == 2

    # Customer F: X post + Gmail message are duplicate evidence -> collapsed to one.
    f_flags = {f.flag_type: f for f in by_customer["CUST-F"].risk_flags}
    assert FlagType.PRODUCT_BUG_OR_OUTAGE in f_flags
    assert f_flags[FlagType.PRODUCT_BUG_OR_OUTAGE].recurrence_count == 1
    assert output.processing_report.n_cross_channel_duplicates_collapsed == 1


def test_external_evidence_retains_provenance(
    sources_config: Node3SourcesConfig, identity_mapping: IdentityMappingConfig
) -> None:
    output = _run(sources_config, identity_mapping)
    seen_sources = set()
    for thread in output.thread_signals:
        assert ":" in thread.thread_id
        assert thread.source in {"x", "gmail"}
        seen_sources.add(thread.source)
        for flag in thread.risk_flags:
            assert flag.evidence.source == thread.source
            assert flag.evidence.message_id.startswith(f"{thread.source}:")
    assert seen_sources == {"x", "gmail"}


def test_multi_source_run_is_deterministic(
    sources_config: Node3SourcesConfig, identity_mapping: IdentityMappingConfig
) -> None:
    first = _run(sources_config, identity_mapping)
    second = _run(sources_config, identity_mapping)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_node4_accepts_external_source_output_unchanged(
    sources_config: Node3SourcesConfig, identity_mapping: IdentityMappingConfig
) -> None:
    node3 = _run(sources_config, identity_mapping)
    node2 = make_node2(
        CUSTOMERS,
        risk_scores=[0.2, 0.4, 0.5, 0.95, 0.3, 0.6, 0.25],
        survival_90=[0.9, 0.7, 0.6, 0.1, 0.8, 0.5, 0.85],
    )
    output = run_node4(node2, node3, load_node4_config("1"))
    assert output.summary_stats.n_customers == len(CUSTOMERS)
    assert all(
        account.combined_risk_level in RiskLevel for account in output.ranked_accounts
    )
    # Positive feedback alone must not make Customer A critical.
    account_a = next(a for a in output.ranked_accounts if a.customer_id == "CUST-A")
    assert account_a.combined_risk_level is not RiskLevel.CRITICAL


def test_node5_reports_external_evidence_references(
    sources_config: Node3SourcesConfig, identity_mapping: IdentityMappingConfig
) -> None:
    node3 = _run(sources_config, identity_mapping)
    node2 = make_node2(
        CUSTOMERS,
        risk_scores=[0.2, 0.4, 0.5, 0.95, 0.3, 0.6, 0.25],
        survival_90=[0.9, 0.7, 0.6, 0.1, 0.8, 0.5, 0.85],
    )
    node4 = run_node4(node2, node3, load_node4_config("1"))
    report = run_node5(
        node4,
        load_node5_config("1"),
        node3_output=node3,
        action_rules=load_action_rules("1"),
    )
    evidence = [
        item
        for account in report.report.priority_accounts
        for item in account.evidence
        if item.source == "node3" and item.node3_reference is not None
    ]
    assert evidence, "expected Node 3 evidence for external-source signals"
    for item in evidence:
        assert item.node3_reference is not None
        assert item.node3_reference.thread_id.startswith(("x:", "gmail:"))
        assert item.node3_reference.message_id.startswith(("x:", "gmail:"))

"""Milestone A — Node 4 validation before generation (architecture §5.4)."""

from __future__ import annotations

from datetime import date

import pytest

from node5.node import run_node5
from node5.validation.node4_validator import (
    Node5ValidationError,
    validate_node4_output,
)
from schemas.node4 import Node4Output
from tests.node5.conftest import REFERENCE_DATE, make_sample_node4


def _codes(result) -> set[str]:
    return {error["code"] for error in result.errors}


def test_valid_sample_passes() -> None:
    result = validate_node4_output(
        make_sample_node4(), expected_reference_date=REFERENCE_DATE
    )
    assert result.ok


def test_reference_date_mismatch_is_rejected() -> None:
    result = validate_node4_output(
        make_sample_node4(), expected_reference_date=date(2020, 1, 1)
    )
    assert "REFERENCE_DATE_MISMATCH" in _codes(result)


def test_score_out_of_range_is_rejected() -> None:
    output = make_sample_node4()
    bad = output.ranked_accounts[0].model_copy(update={"combined_score": 1.5})
    mutated = output.model_copy(update={"ranked_accounts": [bad, *output.ranked_accounts[1:]]})
    assert "SCORE_OUT_OF_RANGE" in _codes(validate_node4_output(mutated))


def test_critical_without_reason_is_rejected() -> None:
    output = make_sample_node4()
    critical_index = next(
        i
        for i, account in enumerate(output.ranked_accounts)
        if account.combined_risk_level.value == "critical"
    )
    stripped = output.ranked_accounts[critical_index].model_copy(
        update={"primary_reasons": []}
    )
    accounts = list(output.ranked_accounts)
    accounts[critical_index] = stripped
    mutated = output.model_copy(update={"ranked_accounts": accounts})
    assert "CRITICAL_WITHOUT_REASON" in _codes(validate_node4_output(mutated))


def test_ranking_defects_are_rejected() -> None:
    output = make_sample_node4()
    duplicated = output.model_copy(
        update={"ranked_accounts": [*output.ranked_accounts, output.ranked_accounts[0]]}
    )
    assert "DUPLICATE_CUSTOMER" in _codes(validate_node4_output(duplicated))

    reordered = list(output.ranked_accounts)
    reordered[0] = reordered[0].model_copy(update={"rank": 99})
    mutated = output.model_copy(update={"ranked_accounts": reordered})
    assert "RANKS_NOT_SEQUENTIAL" in _codes(validate_node4_output(mutated))


def test_duplicate_insufficient_ids_are_rejected() -> None:
    output = make_sample_node4()
    duplicated = output.model_copy(
        update={
            "insufficient_data_accounts": [
                *output.insufficient_data_accounts,
                output.insufficient_data_accounts[0],
            ]
        }
    )
    assert "DUPLICATE_CUSTOMER" in _codes(validate_node4_output(duplicated))


def test_cross_list_membership_is_rejected() -> None:
    output = make_sample_node4()
    moved = output.model_copy(
        update={
            "insufficient_data_accounts": [
                *output.insufficient_data_accounts,
                output.ranked_accounts[0],
            ]
        }
    )
    assert "CROSS_LIST_MEMBERSHIP" in _codes(validate_node4_output(moved))


def test_summary_stats_mismatch_is_rejected() -> None:
    output = make_sample_node4()
    stats = output.summary_stats.model_copy(update={"n_low": 999})
    mutated = output.model_copy(update={"summary_stats": stats})
    assert "SUMMARY_STATS_MISMATCH" in _codes(validate_node4_output(mutated))


def test_run_node5_fails_loudly_on_invalid_node4(node5_config, action_rules) -> None:
    output = make_sample_node4()
    stats = output.summary_stats.model_copy(update={"n_customers": 999})
    mutated: Node4Output = output.model_copy(update={"summary_stats": stats})
    with pytest.raises(Node5ValidationError):
        run_node5(mutated, node5_config, action_rules=action_rules)

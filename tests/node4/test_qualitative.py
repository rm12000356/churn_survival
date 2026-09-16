"""Tasks 5.4 / 5.5 — qualitative scoring, recurrence bonus, strongest signal (§4.5/§4.6)."""

from __future__ import annotations

import pytest

from config.loader import load_node4_config
from node4.qualitative import (
    qualitative_score,
    recurrence_bonus,
    select_strongest,
    strongest_signal_strength,
    top_flags,
)
from schemas.enums import OverallSignalStrength, SupportDataStatus
from tests.node4.conftest import make_flag


@pytest.mark.parametrize(
    ("recurrence", "expected"),
    [(1, 0.00), (2, 0.05), (3, 0.10), (4, 0.15), (5, 0.20), (9, 0.20)],
)
def test_recurrence_bonus_table(recurrence: int, expected: float) -> None:
    # Contract formula is unrounded: min(0.20, 0.05 * max(0, n - 1)).
    assert recurrence_bonus(recurrence) == pytest.approx(expected)
    assert recurrence_bonus(recurrence) == pytest.approx(
        min(0.20, 0.05 * max(0, recurrence - 1))
    )


def test_no_data_scores_zero() -> None:
    config = load_node4_config("1")
    flags = [make_flag("cancellation_intent")]
    assert qualitative_score(flags, SupportDataStatus.NO_DATA, config) == 0.0


def test_no_flags_scores_zero() -> None:
    config = load_node4_config("1")
    assert qualitative_score([], SupportDataStatus.SUFFICIENT_DATA, config) == 0.0


def test_base_score_is_strongest_flag() -> None:
    config = load_node4_config("1")
    flags = [
        make_flag("cancellation_intent", severity="high", signal_strength="strong"),
        make_flag("billing_complaint", severity="high", signal_strength="strong"),
    ]
    assert qualitative_score(flags, SupportDataStatus.SUFFICIENT_DATA, config) == 1.0


def test_recurrence_bonus_applied_and_clamped() -> None:
    config = load_node4_config("1")
    flags = [
        make_flag(
            "cancellation_intent",
            severity="medium",
            signal_strength="moderate",
            recurrence_count=3,
        )
    ]
    assert qualitative_score(flags, SupportDataStatus.SUFFICIENT_DATA, config) == 0.76


def test_positive_feedback_never_reduces_score() -> None:
    config = load_node4_config("1")
    positive_only = [make_flag("positive_feedback", severity="low", signal_strength="strong")]
    assert qualitative_score(positive_only, SupportDataStatus.SUFFICIENT_DATA, config) == 0.0
    with_positive = [
        make_flag("positive_feedback", severity="low", signal_strength="strong"),
        make_flag("billing_complaint", severity="high", signal_strength="strong"),
    ]
    assert qualitative_score(with_positive, SupportDataStatus.SUFFICIENT_DATA, config) == 0.55


def test_strongest_prefers_strength_first() -> None:
    config = load_node4_config("1")
    flags = [
        make_flag("cancellation_intent", severity="low", signal_strength="weak"),
        make_flag("billing_complaint", severity="high", signal_strength="strong"),
    ]
    assert select_strongest(flags, config).flag_type.value == "billing_complaint"


def test_strongest_tie_break_hierarchy_weight() -> None:
    config = load_node4_config("1")
    flags = [
        make_flag("renewal_or_contract_concern", severity="high", signal_strength="strong"),
        make_flag("cancellation_intent", severity="high", signal_strength="strong"),
    ]
    assert select_strongest(flags, config).flag_type.value == "cancellation_intent"


def test_strongest_tie_break_recurrence() -> None:
    config = load_node4_config("1")
    flags = [
        make_flag(
            "product_bug_or_outage",
            severity="high",
            signal_strength="strong",
            recurrence_count=1,
        ),
        make_flag(
            "product_bug_or_outage",
            severity="high",
            signal_strength="strong",
            recurrence_count=4,
        ),
    ]
    assert select_strongest(flags, config).recurrence_count == 4


def test_strongest_tie_break_lexicographic_flag_type() -> None:
    config = load_node4_config("1")
    flags = [
        make_flag("poor_support_experience", severity="high", signal_strength="strong"),
        make_flag("product_bug_or_outage", severity="high", signal_strength="strong"),
    ]
    # "poor_support_experience" < "product_bug_or_outage" lexicographically
    assert select_strongest(flags, config).flag_type.value == "poor_support_experience"


def test_strongest_signal_strength_none_when_empty() -> None:
    config = load_node4_config("1")
    assert select_strongest([], config) is None
    assert strongest_signal_strength([], config) == OverallSignalStrength.NONE


def test_top_flags_ordered_strongest_first_and_serializable() -> None:
    config = load_node4_config("1")
    flags = [
        make_flag("billing_complaint", severity="high", signal_strength="strong"),
        make_flag("cancellation_intent", severity="high", signal_strength="strong"),
    ]
    serialized = top_flags(flags, config)
    assert [item["flag_type"] for item in serialized] == [
        "cancellation_intent",
        "billing_complaint",
    ]
    assert serialized[0]["recurrence_count"] == 1


# --------------------------------------------------------------------------- #
# F-2 — positive feedback is contextual only (architecture §4.2/§4.29)
# --------------------------------------------------------------------------- #
def test_positive_only_is_not_a_strongest_risk_signal() -> None:
    config = load_node4_config("1")
    flags = [make_flag("positive_feedback", severity="high", signal_strength="strong")]
    assert select_strongest(flags, config) is None
    assert strongest_signal_strength(flags, config) == OverallSignalStrength.NONE


def test_positive_only_qualitative_score_is_zero() -> None:
    config = load_node4_config("1")
    for strength in ("weak", "moderate", "strong"):
        flags = [make_flag("positive_feedback", signal_strength=strength, recurrence_count=10)]
        assert qualitative_score(flags, SupportDataStatus.SUFFICIENT_DATA, config) == 0.0


def test_positive_recurrence_does_not_trigger_recurrence_bonus() -> None:
    config = load_node4_config("1")
    flags = [make_flag("positive_feedback", signal_strength="strong", recurrence_count=10)]
    assert recurrence_bonus(flags[0].recurrence_count) > 0  # the bonus itself exists
    assert qualitative_score(flags, SupportDataStatus.SUFFICIENT_DATA, config) == 0.0


def test_positive_feedback_is_excluded_from_top_flags() -> None:
    config = load_node4_config("1")
    flags = [
        make_flag("positive_feedback", severity="high", signal_strength="strong"),
        make_flag("renewal_or_contract_concern", severity="medium", signal_strength="moderate"),
    ]
    assert [item["flag_type"] for item in top_flags(flags, config)] == [
        "renewal_or_contract_concern"
    ]


def test_risk_flag_selected_over_positive_feedback() -> None:
    config = load_node4_config("1")
    flags = [
        make_flag("positive_feedback", severity="high", signal_strength="strong"),
        make_flag("cancellation_intent", severity="medium", signal_strength="moderate"),
    ]
    selected = select_strongest(flags, config)
    assert selected is not None
    assert selected.flag_type.value == "cancellation_intent"


def test_negative_flag_recurrence_used_despite_positive_recurrence() -> None:
    config = load_node4_config("1")
    flags = [
        make_flag(
            "renewal_or_contract_concern",
            severity="medium",
            signal_strength="moderate",
            recurrence_count=1,
        ),
        make_flag(
            "positive_feedback",
            severity="high",
            signal_strength="strong",
            recurrence_count=10,
        ),
    ]
    # base = 0.85 * 0.66 = 0.561, bonus from renewal recurrence 1 = 0.00
    assert qualitative_score(flags, SupportDataStatus.SUFFICIENT_DATA, config) == pytest.approx(
        0.561
    )


# --------------------------------------------------------------------------- #
# F-4 — qualitative score preserves contract precision (no unauthorized round)
# --------------------------------------------------------------------------- #
def test_qualitative_score_preserves_non_round_value() -> None:
    config = load_node4_config("1")
    flags = [make_flag("competitor_mention", signal_strength="weak", recurrence_count=1)]
    # 0.45 * 0.33 = 0.1485 must not be rounded to 0.149
    assert qualitative_score(flags, SupportDataStatus.SUFFICIENT_DATA, config) == pytest.approx(
        0.1485
    )
    assert qualitative_score(flags, SupportDataStatus.SUFFICIENT_DATA, config) != 0.149


"""Task 5.2 / I-14 — Node 2 full-index vs scored-subset alignment (§4.3, D-6).

This is mandatory: incorrect full-index/scored-index mapping can silently
produce plausible but wrong results.
"""

from __future__ import annotations

from config.loader import load_node4_config
from node4.node import _build_alignment, run_node4
from tests.node4.conftest import make_node2, make_node3


def _alignment_fixture():
    return make_node2(
        ["C1", "C2", "C3", "C4", "C5"],
        states=["scored", "not_enough_data", "excluded", "scored", "scored"],
        risk_scores=[0.10, 0.20, 0.30],
        survival_90=[0.90, 0.80, 0.70],
    )


def test_scored_subset_positions_never_leak() -> None:
    alignment = _build_alignment(_alignment_fixture())
    assert alignment.scored_position["C1"] == 0
    assert "C2" not in alignment.scored_position
    assert "C3" not in alignment.scored_position
    assert alignment.scored_position["C4"] == 1
    assert alignment.scored_position["C5"] == 2


def test_full_index_matches_input_order() -> None:
    alignment = _build_alignment(_alignment_fixture())
    assert alignment.full_index == {"C1": 0, "C2": 1, "C3": 2, "C4": 3, "C5": 4}


def test_output_quantitative_values_follow_scored_subset() -> None:
    config = load_node4_config("1")
    output = run_node4(_alignment_fixture(), make_node3([]), config)
    by_id = {a.customer_id: a for a in output.ranked_accounts}
    by_id.update({a.customer_id: a for a in output.insufficient_data_accounts})

    assert by_id["C1"].quantitative.normalized_risk == 0.1
    assert by_id["C4"].quantitative.normalized_risk == 0.2
    assert by_id["C5"].quantitative.normalized_risk == 0.3
    assert by_id["C2"].quantitative.normalized_risk is None
    assert by_id["C3"].quantitative.normalized_risk is None
    assert by_id["C2"].quantitative.customer_state.value == "not_enough_data"
    assert by_id["C3"].quantitative.customer_state.value == "excluded"


def test_no_partial_alignment_error_for_unscored_customers() -> None:
    config = load_node4_config("1")
    output = run_node4(_alignment_fixture(), make_node3([]), config)
    assert all(e["code"] != "PARTIAL_ALIGNMENT" for e in output.processing_report.errors)

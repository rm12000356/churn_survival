"""Tasks 5.9 / D-3 — evidence references, provenance, explicit absence (§4.23)."""

from __future__ import annotations

from config.loader import load_node4_config
from node4.node import run_node4
from tests.node4.conftest import (
    make_association,
    make_flag,
    make_node2,
    make_node3,
    make_signal,
    make_thread,
)


def _account(output):
    return (output.ranked_accounts + output.insufficient_data_accounts)[0]


def test_node2_evidence_preserves_model_version_and_feature_refs() -> None:
    config = load_node4_config("1")
    node2 = make_node2(
        ["A"],
        risk_scores=[0.5],
        survival_90=[0.5],
        model_version="model_abc",
        feature_associations=[
            make_association("usage_frequency", coefficient=0.4, hazard_ratio=1.5)
        ],
    )
    output = run_node4(node2, make_node3([]), config)
    ref = _account(output).evidence_refs.node2
    assert ref.model_version == "model_abc"
    assert ref.customer_state.value == "scored"
    assert ref.feature_refs == ["usage_frequency"]


def test_node3_signal_version_is_deterministic_composite() -> None:
    config = load_node4_config("1")
    node3 = make_node3([make_signal("A", risk_flags=[make_flag("billing_complaint")])])
    output = run_node4(make_node2([]), node3, config)
    ref = _account(output).evidence_refs.node3
    assert ref.signal_version == (
        "n3;pre=pre_v1.0;agg=agg_v1.0;vocab=vocab_v1.0;prompt=prompt_v1.0;model=offline"
    )


def test_node3_evidence_thread_and_message_ids() -> None:
    config = load_node4_config("1")
    node3 = make_node3(
        [make_signal("A", risk_flags=[make_flag("billing_complaint", message_id="msg_1")])],
        thread_signals=[make_thread("T1", "A"), make_thread("T2", "A")],
    )
    output = run_node4(make_node2([]), node3, config)
    ref = _account(output).evidence_refs.node3
    assert ref.thread_ids == ["T1", "T2"]
    assert ref.message_ids == ["msg_1"]


def test_node3_absence_is_explicit() -> None:
    config = load_node4_config("1")
    node2 = make_node2(["A"], risk_scores=[0.5], survival_90=[0.5])
    output = run_node4(node2, make_node3([]), config)
    ref = _account(output).evidence_refs.node3
    assert ref.signal_version == ""
    assert ref.thread_ids == []
    assert ref.message_ids == []


def test_node2_absence_is_explicit() -> None:
    config = load_node4_config("1")
    node3 = make_node3([make_signal("A", risk_flags=[make_flag("billing_complaint")])])
    output = run_node4(None, node3, config)
    ref = _account(output).evidence_refs.node2
    assert ref.model_version == ""
    assert ref.feature_refs == []


def test_evidence_is_not_fabricated_from_other_customers() -> None:
    config = load_node4_config("1")
    node3 = make_node3(
        [
            make_signal("A", risk_flags=[make_flag("billing_complaint", message_id="msg_a")]),
            make_signal("B", risk_flags=[make_flag("cancellation_intent", message_id="msg_b")]),
        ]
    )
    output = run_node4(make_node2([]), node3, config)
    by_id = {a.customer_id: a for a in output.ranked_accounts}
    assert by_id["A"].evidence_refs.node3.message_ids == ["msg_a"]
    assert by_id["B"].evidence_refs.node3.message_ids == ["msg_b"]

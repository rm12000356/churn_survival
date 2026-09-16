"""F-1 regression — evidence resolution is bound to the owning customer.

A foreign reference must never publish another customer's evidence; it produces a
structured mismatch error instead. Covers the eight audit cases.
"""

from __future__ import annotations

from datetime import UTC, datetime

from node5.node import run_node5
from node5.report.evidence import Node3EvidenceIndex, build_evidence
from schemas.enums import EvidenceMode, FlagType
from schemas.node3 import Evidence
from tests.node5.conftest import make_sample_inputs, make_sample_node4

NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)


def _ev(message_id: str, text: str) -> Evidence:
    return Evidence(message_id=message_id, text=text, timestamp=NOW)


def _index() -> Node3EvidenceIndex:
    flag = FlagType.BILLING_COMPLAINT
    return Node3EvidenceIndex(
        messages={
            ("A", "msg_a"): ("thr_A", flag, _ev("msg_a", "A own message")),
            ("A", "msg_shared"): ("thr_A", flag, _ev("msg_shared", "A shared message")),
            ("B", "msg_b"): ("thr_B", flag, _ev("msg_b", "B own message")),
            ("B", "msg_shared"): ("thr_B", flag, _ev("msg_shared", "B shared message")),
            ("", "msg_orphan"): ("thr_orphan", flag, _ev("msg_orphan", "orphan message")),
        },
        owners={
            "msg_a": {"A"},
            "msg_shared": {"A", "B"},
            "msg_b": {"B"},
            "msg_orphan": {""},
        },
    )


def _account(customer_id: str = "A", message_ids=(), thread_ids=()):
    base = next(
        a for a in make_sample_node4().ranked_accounts if a.customer_id == customer_id
    )
    node3_ref = base.evidence_refs.node3.model_copy(
        update={"message_ids": list(message_ids), "thread_ids": list(thread_ids)}
    )
    return base.model_copy(
        update={"evidence_refs": base.evidence_refs.model_copy(update={"node3": node3_ref})}
    )


def _run(account, index=None):
    warnings: list[str] = []
    errors: list[dict] = []
    items = build_evidence(
        account,
        index if index is not None else _index(),
        include=True,
        mode=EvidenceMode.FULL_EVIDENCE,
        max_items=10,
        warnings=warnings,
        errors=errors,
    )
    return items, warnings, errors


def _node3_items(items):
    return [item for item in items if item.source == "node3"]


def test_1_valid_same_customer_message() -> None:
    items, _, errors = _run(_account("A", message_ids=["msg_a"], thread_ids=["thr_A"]))
    assert len(_node3_items(items)) == 1
    assert _node3_items(items)[0].node3_reference.message_id == "msg_a"
    assert errors == []


def test_2_wrong_customer_same_message_id() -> None:
    # B references A's message ID.
    items, _, errors = _run(_account("B", message_ids=["msg_a"], thread_ids=["thr_B"]))
    assert _node3_items(items) == []
    assert any(e["code"] == "EVIDENCE_CUSTOMER_MISMATCH" for e in errors)
    assert "A own message" not in str(items)


def test_3_wrong_customer_different_message_id() -> None:
    # A references B's message ID.
    items, _, errors = _run(_account("A", message_ids=["msg_b"], thread_ids=["thr_A"]))
    assert _node3_items(items) == []
    assert any(e["code"] == "EVIDENCE_CUSTOMER_MISMATCH" for e in errors)


def test_4_wrong_thread() -> None:
    items, _, errors = _run(
        _account("A", message_ids=["msg_a"], thread_ids=["thr_other"])
    )
    assert _node3_items(items) == []
    assert any(e["code"] == "EVIDENCE_THREAD_MISMATCH" for e in errors)


def test_5_wrong_customer_and_wrong_thread() -> None:
    items, _, errors = _run(
        _account("A", message_ids=["msg_b"], thread_ids=["thr_other"])
    )
    assert _node3_items(items) == []
    assert any(e["code"] == "EVIDENCE_CUSTOMER_MISMATCH" for e in errors)


def test_6_missing_customer_id_is_not_published() -> None:
    items, _, errors = _run(_account("A", message_ids=["msg_orphan"], thread_ids=["thr_A"]))
    assert _node3_items(items) == []
    assert any(e["code"] == "EVIDENCE_CUSTOMER_MISMATCH" for e in errors)
    assert "orphan message" not in str(items)


def test_7_duplicate_message_ids_resolve_to_the_right_owner() -> None:
    items_a, _, errors_a = _run(
        _account("A", message_ids=["msg_shared"], thread_ids=["thr_A"])
    )
    items_b, _, errors_b = _run(
        _account("B", message_ids=["msg_shared"], thread_ids=["thr_B"])
    )
    assert _node3_items(items_a)[0].node3_reference.evidence_text == "A shared message"
    assert _node3_items(items_b)[0].node3_reference.evidence_text == "B shared message"
    assert errors_a == [] and errors_b == []


def test_8_cross_customer_evidence_in_ranked_and_insufficient(node5_config, action_rules) -> None:
    node4, node3 = make_sample_inputs()

    def with_foreign(account):
        node3_ref = account.evidence_refs.node3.model_copy(
            update={
                "message_ids": [
                    *account.evidence_refs.node3.message_ids,
                    "msg_billing_complaint",
                ]
            }
        )
        return account.model_copy(
            update={
                "evidence_refs": account.evidence_refs.model_copy(
                    update={"node3": node3_ref}
                )
            }
        )

    ranked = [with_foreign(node4.ranked_accounts[0]), *node4.ranked_accounts[1:]]
    insufficient = [
        with_foreign(node4.insufficient_data_accounts[0]),
        *node4.insufficient_data_accounts[1:],
    ]
    mutated = node4.model_copy(
        update={"ranked_accounts": ranked, "insufficient_data_accounts": insufficient}
    )
    output = run_node5(
        mutated, node5_config, node3_output=node3, action_rules=action_rules
    )
    mismatches = [
        error
        for error in output.processing_report.errors
        if error["code"] == "EVIDENCE_CUSTOMER_MISMATCH"
    ]
    assert {"A", "E"} <= {error["customer_id"] for error in mismatches}
    # Only A and E referenced the foreign message; B's own evidence is legitimate.
    by_id = {
        report.customer_id: report
        for report in [
            *output.report.priority_accounts,
            *output.report.insufficient_data_accounts,
        ]
    }
    for customer_id in ("A", "E"):
        for item in by_id[customer_id].evidence:
            if item.node3_reference is not None:
                assert item.node3_reference.message_id != "msg_billing_complaint"

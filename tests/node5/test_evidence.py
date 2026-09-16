"""Milestone A — evidence lookup + privacy modes (architecture §5.11–§5.13, D-U1/D-U6)."""

from __future__ import annotations

from datetime import UTC, datetime

from node5.report.evidence import Node3EvidenceIndex, build_evidence
from schemas.enums import EvidenceMode, FlagType
from schemas.node3 import Evidence
from tests.node5.conftest import make_sample_node4

NOW = datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC)
LONG_TEXT = "We are cancelling " + "because of repeated failures " * 20


def _index(text: str = LONG_TEXT) -> Node3EvidenceIndex:
    return Node3EvidenceIndex(
        messages={
            ("A", "msg_cancellation_intent"): (
                "thr_A",
                FlagType.CANCELLATION_INTENT,
                Evidence(
                    message_id="msg_cancellation_intent", text=text, timestamp=NOW
                ),
            )
        },
        owners={"msg_cancellation_intent": {"A"}},
    )


def _account():
    return make_sample_node4().ranked_accounts[0]


def _build(mode: EvidenceMode, *, include: bool = True, max_items: int = 5, index=None):
    warnings: list[str] = []
    items = build_evidence(
        _account(),
        index if index is not None else _index(),
        include=include,
        mode=mode,
        max_items=max_items,
        warnings=warnings,
    )
    return items, warnings


def test_disabled_mode_emits_no_evidence() -> None:
    items, _ = _build(EvidenceMode.DISABLED)
    assert items == []


def test_include_false_wins_over_mode() -> None:
    items, _ = _build(EvidenceMode.FULL_EVIDENCE, include=False)
    assert items == []


def test_summary_only_has_no_quote() -> None:
    items, _ = _build(EvidenceMode.SUMMARY_ONLY)
    node3_items = [item for item in items if item.source == "node3"]
    assert node3_items
    assert all(item.node3_reference is None for item in node3_items)


def test_short_quote_truncates() -> None:
    items, _ = _build(EvidenceMode.SHORT_QUOTE)
    node3 = next(item for item in items if item.source == "node3")
    assert node3.node3_reference is not None
    assert node3.node3_reference.evidence_text.endswith("…")


def test_full_evidence_keeps_text() -> None:
    items, _ = _build(EvidenceMode.FULL_EVIDENCE)
    node3 = next(item for item in items if item.source == "node3")
    assert node3.node3_reference is not None
    assert node3.node3_reference.evidence_text == LONG_TEXT


def test_max_items_caps_and_unresolved_warns() -> None:
    items, warnings = _build(EvidenceMode.SHORT_QUOTE, max_items=1, index=Node3EvidenceIndex())
    assert len(items) <= 1
    assert any("could not be resolved" in warning for warning in warnings)

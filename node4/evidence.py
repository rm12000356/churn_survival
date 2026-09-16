"""Node 4 evidence references (architecture §4.23, D-3, ROADMAP Task 5.9).

Preserves the upstream provenance needed to reconstruct a decision: Node 2 model
version / customer state / feature references, and Node 3 signal version /
thread IDs / message IDs. Never fabricates evidence — absence is represented by
empty lists and an empty signal version.
"""

from __future__ import annotations

from collections.abc import Sequence

from schemas.enums import CustomerState
from schemas.node3 import AggregatedRiskFlag, CustomerSupportSignals, CustomerSupportSignalsMeta
from schemas.node4 import Node2EvidenceRef, Node3EvidenceRef


def signal_version(meta: CustomerSupportSignalsMeta) -> str:
    """D-3: deterministic composite of the customer's Node 3 meta, fixed field order."""
    return (
        f"n3;pre={meta.preprocessing_version}"
        f";agg={meta.aggregation_version}"
        f";vocab={meta.vocabulary_version}"
        f";prompt={meta.prompt_version}"
        f";model={meta.model_version}"
    )


def node2_evidence(
    model_version: str,
    customer_state: CustomerState,
    feature_refs: Sequence[str],
) -> Node2EvidenceRef:
    return Node2EvidenceRef(
        model_version=model_version,
        customer_state=customer_state,
        feature_refs=list(feature_refs),
    )


def message_ids(signal: CustomerSupportSignals | None) -> list[str]:
    """Ordered unique message IDs from the customer's aggregated flags (no invention)."""
    if signal is None:
        return []
    ids: list[str] = []
    for flag in signal.risk_flags:
        for message_id in [*flag.evidence_message_ids, flag.strongest_evidence.message_id]:
            if message_id not in ids:
                ids.append(message_id)
    return ids


def node3_evidence(
    signal: CustomerSupportSignals | None,
    thread_ids: Sequence[str] | None = None,
) -> Node3EvidenceRef:
    if signal is None:
        return Node3EvidenceRef(signal_version="", thread_ids=[], message_ids=[])
    return Node3EvidenceRef(
        signal_version=signal_version(signal.meta),
        thread_ids=list(thread_ids or []),
        message_ids=message_ids(signal),
    )


def flag_evidence(flag: AggregatedRiskFlag) -> dict[str, object]:
    """Reconstructable per-flag evidence reference used by structured reasons."""
    return {
        "flag_type": flag.flag_type.value,
        "severity": flag.severity.value,
        "signal_strength": flag.signal_strength.value,
        "recurrence_count": flag.recurrence_count,
        "message_ids": list(flag.evidence_message_ids),
    }

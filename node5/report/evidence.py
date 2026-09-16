"""Evidence representation with privacy modes (architecture §5.11/§5.12/§5.13).

Node 3 is used strictly as an **evidence lookup source** (D-U1). Node 5 resolves
only message IDs that Node 4 already references; it never introduces new
evidence, never invents a quotation, and records a structured error/warning when
a referenced item cannot be resolved.

**F-1 (customer binding).** Evidence is keyed by ``(customer_id, message_id)``,
never by ``message_id`` alone. A message owned by another customer — or resolving
to a thread the account does not own — is never published: it produces a
structured ``EVIDENCE_CUSTOMER_MISMATCH`` / ``EVIDENCE_THREAD_MISMATCH`` error
and the evidence is omitted. Foreign text/timestamp/thread/flag is never leaked.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from schemas.enums import EvidenceMode, FlagType
from schemas.node3 import Evidence, Node3Output
from schemas.node4 import RankedAccount
from schemas.node5 import (
    Node2ReportReference,
    Node3ReportReference,
    ReportEvidence,
)

_SHORT_QUOTE_LIMIT = 160


@dataclass
class Node3EvidenceIndex:
    """Customer-bound Node 3 evidence lookup (F-1).

    ``messages`` maps ``(customer_id, message_id)`` to the owning thread,
    flag type, and evidence. ``owners`` maps a message ID to the set of customer
    IDs that own it, so a foreign reference can be detected (and never published)
    even when the ID is not resolvable for the referencing account.
    """

    messages: dict[tuple[str, str], tuple[str, FlagType, Evidence]] = field(
        default_factory=dict
    )
    owners: dict[str, set[str]] = field(default_factory=dict)


def build_node3_index(node3_output: Node3Output | None) -> Node3EvidenceIndex:
    """Index Node 3 thread-level evidence, bound to its owning customer (F-1)."""
    index = Node3EvidenceIndex()
    if node3_output is None:
        return index
    for thread in node3_output.thread_signals:
        customer_id = thread.customer_id
        for flag in thread.risk_flags:
            message_ids = [*flag.evidence_message_ids, flag.evidence.message_id]
            for message_id in message_ids:
                index.messages.setdefault(
                    (customer_id, message_id),
                    (thread.thread_id, flag.flag_type, flag.evidence),
                )
                index.owners.setdefault(message_id, set()).add(customer_id)
    return index


def _short_quote(text: str) -> str:
    """Deterministically shorten a quote without altering its meaning/words."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= _SHORT_QUOTE_LIMIT:
        return collapsed
    clipped = collapsed[:_SHORT_QUOTE_LIMIT].rsplit(" ", 1)[0]
    return f"{clipped}…"


def _node2_evidence(account: RankedAccount) -> ReportEvidence | None:
    model_version = account.evidence_refs.node2.model_version
    drivers = account.quantitative.top_drivers
    if not model_version:
        return None
    if drivers:
        description = "Primary model drivers: " + ", ".join(drivers) + "."
    else:
        description = "Quantitative survival-model result."
    return ReportEvidence(
        source="node2",
        description=description,
        node2_reference=Node2ReportReference(
            model_version=model_version,
            feature_ref=drivers[0] if drivers else None,
        ),
    )


def _mismatch_error(code: str, account: RankedAccount, detail: str) -> dict[str, Any]:
    return {
        "code": code,
        "customer_id": account.customer_id,
        "source": "node3",
        "message": detail,
    }


def _node3_evidence(
    account: RankedAccount,
    index: Node3EvidenceIndex,
    mode: EvidenceMode,
    warnings: list[str],
    errors: list[dict[str, Any]],
) -> list[ReportEvidence]:
    items: list[ReportEvidence] = []
    unresolved: list[str] = []
    allowed_threads = set(account.evidence_refs.node3.thread_ids)
    for message_id in account.evidence_refs.node3.message_ids:
        resolved = index.messages.get((account.customer_id, message_id))
        if resolved is None:
            owners = index.owners.get(message_id)
            if owners:
                # The ID exists, but under a different customer: never publish.
                errors.append(
                    _mismatch_error(
                        "EVIDENCE_CUSTOMER_MISMATCH",
                        account,
                        f"referenced message {message_id!r} is owned by a different "
                        f"customer ({sorted(owners)}); evidence omitted",
                    )
                )
            else:
                unresolved.append(message_id)
            continue
        thread_id, flag_type, evidence = resolved
        if allowed_threads and thread_id not in allowed_threads:
            # Customer-owned message resolving to an unowned thread: never publish.
            errors.append(
                _mismatch_error(
                    "EVIDENCE_THREAD_MISMATCH",
                    account,
                    f"referenced message {message_id!r} belongs to thread "
                    f"{thread_id!r}, which the account does not own; evidence omitted",
                )
            )
            continue
        label = flag_type.value.replace("_", " ").capitalize()
        description = f"{label} reported in a support interaction."
        if mode in (EvidenceMode.SHORT_QUOTE, EvidenceMode.FULL_EVIDENCE):
            text = (
                _short_quote(evidence.text)
                if mode == EvidenceMode.SHORT_QUOTE
                else evidence.text
            )
            items.append(
                ReportEvidence(
                    source="node3",
                    description=description,
                    node3_reference=Node3ReportReference(
                        thread_id=thread_id,
                        message_id=message_id,
                        timestamp=evidence.timestamp,
                        evidence_text=text,
                    ),
                )
            )
        else:  # SUMMARY_ONLY (DISABLED handled by caller)
            items.append(ReportEvidence(source="node3", description=description))
    if unresolved:
        warnings.append(
            f"{len(unresolved)} referenced Node 3 evidence item(s) could not be resolved "
            "and were omitted (no evidence was invented)."
        )
    return items


def build_evidence(
    account: RankedAccount,
    index: Node3EvidenceIndex,
    *,
    include: bool,
    mode: EvidenceMode,
    max_items: int,
    warnings: list[str],
    errors: list[dict[str, Any]] | None = None,
) -> list[ReportEvidence]:
    """Build the account's evidence list respecting the evidence policy (D-U6)."""
    errors = errors if errors is not None else []
    if not include or mode == EvidenceMode.DISABLED or max_items <= 0:
        return []
    candidates: list[ReportEvidence] = []
    node2 = _node2_evidence(account)
    if node2 is not None:
        candidates.append(node2)
    candidates.extend(_node3_evidence(account, index, mode, warnings, errors))
    return candidates[:max_items]

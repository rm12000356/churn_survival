"""Normalize external messages into the existing ``SupportThread`` contract.

Grouping is deterministic: messages are grouped by ``(source, thread_id)`` and a
thread is owned by the single distinct resolved customer among its customer
messages. A thread with no resolved customer, or with messages from more than one
customer, is dropped with a structured error — never attached to a customer.

Source-native ids are namespaced as ``"{source}:{raw_id}"`` so ids stay globally
unique across sources and provenance survives into Node 4/Node 5 evidence
references unchanged. The referenced raw id remains recoverable (``split(":", 1)``).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field

from schemas.external import ExternalMessage
from schemas.node3 import SupportMessage, SupportThread

DEFAULT_CHANNELS: Mapping[str, str] = {
    "x": "x",
    "gmail": "email",
}


@dataclass
class NormalizationResult:
    """Normalized threads plus structured errors for dropped groups."""

    threads: list[SupportThread] = field(default_factory=list)
    errors: list[dict[str, object]] = field(default_factory=list)


def namespace_id(source: str, raw_id: str) -> str:
    """Deterministic global id: ``"{source}:{raw_id}"``."""
    return f"{source}:{raw_id}"


def normalize_threads(
    messages: list[ExternalMessage] | tuple[ExternalMessage, ...],
    *,
    channel_by_source: Mapping[str, str] | None = None,
) -> NormalizationResult:
    """Group attached messages into ``SupportThread`` records."""
    channels = dict(DEFAULT_CHANNELS)
    if channel_by_source:
        channels.update(channel_by_source)

    groups: dict[tuple[str, str], list[ExternalMessage]] = defaultdict(list)
    for message in messages:
        groups[(message.source, message.thread_id)].append(message)

    result = NormalizationResult()
    for (source, thread_id), group in sorted(groups.items()):
        ordered = sorted(group, key=lambda m: (m.timestamp, m.message_id))
        owners = {
            message.customer_id
            for message in ordered
            if message.role == "customer" and message.customer_id
        }
        if not owners:
            result.errors.append(
                {
                    "source": source,
                    "thread_id": thread_id,
                    "code": "UNRESOLVED_THREAD",
                    "detail": "thread dropped: no customer message could be mapped",
                }
            )
            continue
        if len(owners) > 1:
            result.errors.append(
                {
                    "source": source,
                    "thread_id": thread_id,
                    "code": "CROSS_CUSTOMER_CONTAMINATION",
                    "detail": (
                        "thread dropped: messages resolve to multiple customers "
                        f"({sorted(owners)})"
                    ),
                }
            )
            continue

        customer_id = next(iter(owners))
        subject = next((message.subject for message in ordered if message.subject), None)
        result.threads.append(
            SupportThread(
                thread_id=namespace_id(source, thread_id),
                customer_id=customer_id,
                created_at=ordered[0].timestamp,
                closed_at=None,
                channel=channels.get(source, source),
                subject=subject,
                status=None,
                tags=None,
                language=None,
                duplicate_of=None,
                source=source,
                messages=[
                    SupportMessage(
                        message_id=namespace_id(source, message.message_id),
                        timestamp=message.timestamp,
                        role=message.role,
                        text=message.text,
                    )
                    for message in ordered
                ],
            )
        )

    result.threads.sort(key=lambda t: (t.customer_id, t.created_at, t.thread_id))
    return result

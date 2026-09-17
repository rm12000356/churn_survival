"""Support/external namespaced-id collision detection (multi-source addendum §5 QA F-8).

External ids are namespaced ``"{source}:{raw_id}"``. Because support data can
contain arbitrary ids, an external X message with raw id ``foo`` can collide with
a support thread already named ``x:foo``. A collision would make evidence
references (and downstream Node 4/Node 5 lookups) ambiguous.

Rather than change the id format (which Node 5 depends on), collisions are
detected deterministically before merging: a colliding *external* thread is
dropped with a structured ``ID_COLLISION`` error. Records are never overwritten
and non-colliding ids are preserved exactly.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import ValidationError

from schemas.node3 import SupportThread


@dataclass
class CollisionOutcome:
    """Kept external threads plus structured collision errors."""

    external_threads: list[SupportThread] = field(default_factory=list)
    errors: list[dict[str, object]] = field(default_factory=list)


def _collect_support_ids(
    support_data: Sequence[SupportThread | dict[str, object]],
) -> set[str]:
    ids: set[str] = set()
    for raw in support_data:
        if isinstance(raw, SupportThread):
            thread = raw
        else:
            try:
                thread = SupportThread.model_validate(raw)
            except ValidationError:
                continue
        ids.add(thread.thread_id)
        ids.update(message.message_id for message in thread.messages)
    return ids


def resolve_support_external_id_collisions(
    support_data: Sequence[SupportThread | dict[str, object]],
    external_threads: Sequence[SupportThread],
) -> CollisionOutcome:
    """Drop external threads whose thread/message ids collide with existing data."""
    support_ids = _collect_support_ids(support_data)
    outcome = CollisionOutcome()
    seen_external_thread_ids: set[str] = set()

    for thread in external_threads:
        collisions: list[str] = []
        if thread.thread_id in support_ids:
            collisions.append(f"thread_id={thread.thread_id!r}")
        if thread.thread_id in seen_external_thread_ids:
            collisions.append(f"duplicate_external_thread_id={thread.thread_id!r}")
        for message in thread.messages:
            if message.message_id in support_ids:
                collisions.append(f"message_id={message.message_id!r}")

        if collisions:
            outcome.errors.append(
                {
                    "source": thread.source,
                    "thread_id": thread.thread_id,
                    "code": "ID_COLLISION",
                    "detail": (
                        "external thread dropped: identifier collides with existing "
                        "data: " + ", ".join(collisions)
                    ),
                }
            )
            continue

        seen_external_thread_ids.add(thread.thread_id)
        outcome.external_threads.append(thread)

    return outcome

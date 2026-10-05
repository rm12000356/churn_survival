from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from pydantic import ValidationError

from schemas.node3 import SupportThread


@dataclass
class CollisionOutcome:
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

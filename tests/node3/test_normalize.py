"""External-message normalization tests (multi-source addendum §5/§17/§22)."""

from __future__ import annotations

from datetime import UTC, datetime

from node3.sources.normalize import namespace_id, normalize_threads
from schemas.external import ExternalMessage


def _message(
    *,
    source: str = "x",
    thread_id: str = "thread-1",
    message_id: str = "m1",
    customer_id: str | None = "CUST-A",
    role: str = "customer",
    timestamp: datetime = datetime(2026, 8, 10, 10, 0, tzinfo=UTC),
    text: str = "hello",
    subject: str | None = None,
) -> ExternalMessage:
    return ExternalMessage(
        source=source,
        external_identity="identity",
        customer_id=customer_id,
        thread_id=thread_id,
        message_id=message_id,
        timestamp=timestamp,
        text=text,
        role=role,  # type: ignore[arg-type]
        subject=subject,
    )


def test_namespaces_ids_and_orders_messages() -> None:
    result = normalize_threads(
        [
            _message(message_id="m2", timestamp=datetime(2026, 8, 10, 11, 0, tzinfo=UTC)),
            _message(message_id="m1", timestamp=datetime(2026, 8, 10, 10, 0, tzinfo=UTC)),
        ]
    )
    thread = result.threads[0]
    assert thread.thread_id == "x:thread-1"
    assert thread.source == "x"
    assert thread.channel == "x"
    assert [m.message_id for m in thread.messages] == ["x:m1", "x:m2"]
    assert thread.created_at == datetime(2026, 8, 10, 10, 0, tzinfo=UTC)


def test_gmail_channel_is_email_and_subject_preserved() -> None:
    result = normalize_threads(
        [_message(source="gmail", subject="Evaluating other vendors", customer_id="CUST-C")]
    )
    assert result.threads[0].channel == "email"
    assert result.threads[0].subject == "Evaluating other vendors"


def test_thread_without_resolved_customer_is_dropped() -> None:
    result = normalize_threads([_message(customer_id=None, role="agent")])
    assert result.threads == []
    assert result.errors[0]["code"] == "UNRESOLVED_THREAD"


def test_cross_customer_thread_is_dropped() -> None:
    result = normalize_threads(
        [
            _message(message_id="a", customer_id="CUST-A"),
            _message(message_id="b", customer_id="CUST-B", timestamp=datetime(
                2026, 8, 10, 10, 5, tzinfo=UTC
            )),
        ]
    )
    assert result.threads == []
    assert result.errors[0]["code"] == "CROSS_CUSTOMER_CONTAMINATION"


def test_namespace_helper_round_trips() -> None:
    namespaced = namespace_id("gmail", "thread-9")
    assert namespaced == "gmail:thread-9"
    assert namespaced.split(":", 1) == ["gmail", "thread-9"]

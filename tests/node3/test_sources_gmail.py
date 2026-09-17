"""Mock/live Gmail source adapter tests (multi-source addendum §8/§22)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from node3.sources.errors import (
    SourceDataError,
    SourceNotConfiguredError,
    SourceNotImplementedError,
)
from node3.sources.gmail_source import GmailSource, MockGmailSource
from tests.node3.conftest import MOCK_SOURCES


def _mock() -> MockGmailSource:
    return MockGmailSource(MOCK_SOURCES / "gmail", agent_identities=["support@acme.com"])


def test_mock_gmail_loads_messages_with_roles_and_subjects() -> None:
    messages = _mock().fetch_customer_data()
    assert messages
    customer = next(m for m in messages if m.message_id == "gmail-msg-001")
    assert customer.source == "gmail"
    assert customer.role == "customer"
    assert customer.external_identity == "cust.c@example.com"
    assert customer.subject == "Evaluating other vendors"
    agent = next(m for m in messages if m.message_id == "gmail-msg-003")
    assert agent.role == "agent"


def test_mock_gmail_missing_metadata_is_tolerated() -> None:
    message = next(
        m for m in _mock().fetch_customer_data() if m.message_id == "gmail-msg-004"
    )
    assert message.subject is None  # thread has no subject
    assert message.text  # body is still usable


def test_mock_gmail_empty_directory_returns_empty(tmp_path: Path) -> None:
    assert MockGmailSource(tmp_path).fetch_customer_data() == []


def test_mock_gmail_missing_directory_raises() -> None:
    with pytest.raises(SourceDataError):
        MockGmailSource(MOCK_SOURCES / "does-not-exist")


def test_mock_gmail_malformed_entry_raises(tmp_path: Path) -> None:
    (tmp_path / "messages.json").write_text(
        json.dumps([{"id": "g1", "thread_id": "t1"}]), encoding="utf-8"
    )
    with pytest.raises(SourceDataError):
        MockGmailSource(tmp_path).fetch_customer_data()


def test_live_gmail_missing_credentials_fails_loudly() -> None:
    with pytest.raises(SourceNotConfiguredError) as exc:
        GmailSource(client_id=None, client_secret=None, refresh_token=None).fetch_customer_data()
    assert "GMAIL_CLIENT_ID" in str(exc.value)


def test_live_gmail_transport_deferred() -> None:
    source = GmailSource(client_id="id", client_secret="secret", refresh_token="refresh")
    with pytest.raises(SourceNotImplementedError):
        source.fetch_customer_data()

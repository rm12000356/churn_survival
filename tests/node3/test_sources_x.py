"""Mock/live X source adapter tests (multi-source addendum §7/§22)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from node3.sources.errors import (
    SourceDataError,
    SourceNotConfiguredError,
    SourceNotImplementedError,
)
from node3.sources.x_source import MockXSource, XSource
from tests.node3.conftest import MOCK_SOURCES


def _mock(**kwargs: object) -> MockXSource:
    defaults: dict[str, object] = {
        "include_public": True,
        "include_dms": True,
        "agent_identities": ["acme_support"],
    }
    defaults.update(kwargs)
    return MockXSource(MOCK_SOURCES / "x", **defaults)  # type: ignore[arg-type]


def test_mock_x_loads_posts_mentions_and_dms() -> None:
    messages = _mock().fetch_customer_data()
    assert {"post", "mention", "dm"} <= {m.source_type for m in messages}
    post = next(m for m in messages if m.message_id == "x-post-001")
    assert post.source == "x"
    assert post.role == "customer"
    assert post.customer_id is None  # unresolved until identity mapping
    assert post.text


def test_mock_x_public_only_excludes_dms() -> None:
    messages = _mock(include_public=True, include_dms=False).fetch_customer_data()
    assert messages
    assert all(m.source_type != "dm" for m in messages)


def test_mock_x_dms_only_and_agent_role() -> None:
    messages = _mock(include_public=False, include_dms=True).fetch_customer_data()
    assert messages
    assert all(m.source_type == "dm" for m in messages)
    agent_messages = [m for m in messages if m.author_id == "acme_support"]
    assert agent_messages and all(m.role == "agent" for m in agent_messages)


def test_mock_x_empty_directory_returns_empty(tmp_path: Path) -> None:
    assert MockXSource(tmp_path).fetch_customer_data() == []


def test_mock_x_missing_directory_raises() -> None:
    with pytest.raises(SourceDataError):
        MockXSource(MOCK_SOURCES / "does-not-exist")


def test_mock_x_malformed_entry_raises(tmp_path: Path) -> None:
    (tmp_path / "posts.json").write_text(json.dumps([{"id": "x-only"}]), encoding="utf-8")
    with pytest.raises(SourceDataError):
        MockXSource(tmp_path).fetch_customer_data()


def test_mock_x_non_list_fixture_raises(tmp_path: Path) -> None:
    (tmp_path / "posts.json").write_text(json.dumps({"id": "x"}), encoding="utf-8")
    with pytest.raises(SourceDataError):
        MockXSource(tmp_path).fetch_customer_data()


def test_live_x_missing_token_fails_loudly() -> None:
    with pytest.raises(SourceNotConfiguredError):
        XSource(access_token=None).fetch_customer_data()


def test_live_x_transport_deferred() -> None:
    with pytest.raises(SourceNotImplementedError):
        XSource(access_token="token-value").fetch_customer_data()

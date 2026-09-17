"""Deterministic identity resolution tests (multi-source addendum §6/§22)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from config.models import IdentityMappingConfig
from node3.sources.identity import resolve_identities
from schemas.external import ExternalMessage

TS = datetime(2026, 8, 10, tzinfo=UTC)


def _message(
    external_identity: str,
    *,
    source: str = "x",
    role: str = "customer",
    customer_id: str | None = None,
    message_id: str = "m1",
) -> ExternalMessage:
    return ExternalMessage(
        source=source,
        external_identity=external_identity,
        customer_id=customer_id,
        thread_id="t1",
        message_id=message_id,
        timestamp=TS,
        text="hello",
        role=role,  # type: ignore[arg-type]
    )


def test_valid_mapping_attaches_customer(identity_mapping: IdentityMappingConfig) -> None:
    result = resolve_identities([_message("x_user_a")], identity_mapping)
    assert not result.unresolved
    assert result.messages[0].customer_id == "CUST-A"


def test_missing_mapping_is_dropped_with_structured_error(
    identity_mapping: IdentityMappingConfig,
) -> None:
    result = resolve_identities([_message("x_user_unknown")], identity_mapping)
    assert result.messages == []
    assert len(result.unresolved) == 1
    assert result.errors[0]["code"] == "UNMAPPED_EXTERNAL_IDENTITY"


def test_identity_mapping_is_authoritative_over_preset_customer(
    identity_mapping: IdentityMappingConfig,
) -> None:
    result = resolve_identities([_message("x_user_a", customer_id="CUST-Z")], identity_mapping)
    assert result.messages[0].customer_id == "CUST-A"


def test_agent_identity_is_never_attached_to_a_customer(
    identity_mapping: IdentityMappingConfig,
) -> None:
    result = resolve_identities(
        [_message("acme_support", role="customer")],
        identity_mapping,
        agent_identities={"x": ["acme_support"]},
    )
    assert not result.unresolved
    assert result.messages[0].role == "agent"
    assert result.messages[0].customer_id is None


def test_blank_identity_is_dropped(identity_mapping: IdentityMappingConfig) -> None:
    result = resolve_identities([_message("   ")], identity_mapping)
    assert result.messages == []
    assert result.errors[0]["code"] == "EXTERNAL_IDENTITY_MISSING"


def test_cross_customer_isolation(identity_mapping: IdentityMappingConfig) -> None:
    result = resolve_identities(
        [_message("x_user_a", message_id="a"), _message("x_user_b", message_id="b")],
        identity_mapping,
    )
    by_id = {m.message_id: m.customer_id for m in result.messages}
    assert by_id == {"a": "CUST-A", "b": "CUST-B"}


def test_gmail_identity_lookup(identity_mapping: IdentityMappingConfig) -> None:
    result = resolve_identities(
        [_message("cust.c@example.com", source="gmail")], identity_mapping
    )
    assert result.messages[0].customer_id == "CUST-C"


def test_empty_identity_mapping_values_rejected() -> None:
    with pytest.raises(ValidationError):
        IdentityMappingConfig(
            mapping_version="bad", mappings={"x": {"x_user_a": "   "}}
        )


def test_agent_and_customer_role_messages_pass_through(
    identity_mapping: IdentityMappingConfig,
) -> None:
    result = resolve_identities(
        [
            _message("support@acme.com", source="gmail", role="agent"),
            _message("cust.c@example.com", source="gmail", role="customer"),
        ],
        identity_mapping,
    )
    roles = {m.role for m in result.messages}
    assert roles == {"agent", "customer"}

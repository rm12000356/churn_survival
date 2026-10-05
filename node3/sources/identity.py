from __future__ import annotations

import hashlib
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field

from config.models import IdentityMappingConfig
from schemas.external import ExternalMessage

_CASE_INSENSITIVE_SOURCES: frozenset[str] = frozenset({"gmail"})


def _identity_key(source: str, identity: str) -> str:
    value = identity.strip()
    if source in _CASE_INSENSITIVE_SOURCES:
        return value.casefold()
    return value


def _identity_reference(identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def _normalized_mappings(
    mapping: IdentityMappingConfig,
) -> tuple[dict[str, dict[str, str]], set[tuple[str, str]]]:
    normalized: dict[str, dict[str, str]] = {}
    ambiguous: set[tuple[str, str]] = set()
    for source, pairs in mapping.mappings.items():
        by_key: dict[str, str] = {}
        for identity, customer_id in pairs.items():
            key = _identity_key(source, identity)
            if key in by_key and by_key[key] != customer_id:
                ambiguous.add((source, key))
            by_key[key] = customer_id
        normalized[source] = by_key
    return normalized, ambiguous


@dataclass
class IdentityResolution:
    messages: list[ExternalMessage] = field(default_factory=list)
    unresolved: list[ExternalMessage] = field(default_factory=list)
    errors: list[dict[str, object]] = field(default_factory=list)


def _agent_set(agent_identities: Mapping[str, Collection[str]]) -> dict[str, frozenset[str]]:
    return {
        source: frozenset(
            identity.strip().casefold() for identity in identities if identity.strip()
        )
        for source, identities in agent_identities.items()
    }


def resolve_identities(
    messages: Sequence[ExternalMessage],
    mapping: IdentityMappingConfig,
    *,
    agent_identities: Mapping[str, Collection[str]] | None = None,
) -> IdentityResolution:
    agent_by_source = _agent_set(agent_identities or {})
    normalized, ambiguous = _normalized_mappings(mapping)
    result = IdentityResolution()

    for index, message in enumerate(messages):
        identity = (message.external_identity or "").strip()
        source_agents = agent_by_source.get(message.source, frozenset())

        if not identity:
            result.unresolved.append(message)
            result.errors.append(
                {
                    "index": index,
                    "source": message.source,
                    "message_id": message.message_id,
                    "code": "EXTERNAL_IDENTITY_MISSING",
                    "detail": "message dropped: external_identity is missing or blank",
                }
            )
            continue

        if identity.casefold() in source_agents:
            result.messages.append(
                message.model_copy(update={"role": "agent", "customer_id": None})
            )
            continue

        if message.role in ("agent", "system"):
            result.messages.append(message)
            continue

        key = _identity_key(message.source, identity)
        if (message.source, key) in ambiguous:
            result.unresolved.append(message)
            result.errors.append(
                {
                    "index": index,
                    "source": message.source,
                    "message_id": message.message_id,
                    "code": "IDENTITY_MAPPING_AMBIGUOUS",
                    "detail": (
                        f"identity {_identity_reference(key)} for source "
                        f"{message.source!r} maps to multiple customers; message was not "
                        "attached to any customer"
                    ),
                }
            )
            continue

        customer_id = normalized.get(message.source, {}).get(key)
        if not customer_id:
            result.unresolved.append(message)
            result.errors.append(
                {
                    "index": index,
                    "source": message.source,
                    "message_id": message.message_id,
                    "code": "UNMAPPED_EXTERNAL_IDENTITY",
                    "detail": (
                        f"message from {message.source!r} identity "
                        f"{_identity_reference(identity)} is not in the identity mapping; "
                        "it was not attached to any customer"
                    ),
                }
            )
            continue

        result.messages.append(message.model_copy(update={"customer_id": customer_id}))

    return result

"""Deterministic customer identity resolution (multi-source addendum §6).

External identities (X handles, Gmail addresses, ...) are mapped to canonical
``customer_id`` values through an explicit, versioned, exact-match configuration.
There is **no** fuzzy matching and **no** LLM involvement. A message that cannot
be safely mapped is never attached to a customer: it is dropped from the batch and
recorded as a structured error. False customer association is worse than missing
evidence.
"""

from __future__ import annotations

import hashlib
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field

from config.models import IdentityMappingConfig
from schemas.external import ExternalMessage

# Per-source identity normalization (QA F-11): Gmail addresses are treated
# case-insensitively (domains are case-insensitive and Gmail ignores local-part
# case); X identities keep exact case. Matching always fails closed: an identity
# that cannot be resolved unambiguously is dropped, never cross-attached.
_CASE_INSENSITIVE_SOURCES: frozenset[str] = frozenset({"gmail"})


def _identity_key(source: str, identity: str) -> str:
    """Normalized lookup key for ``identity`` under ``source``'s matching rule."""
    value = identity.strip()
    if source in _CASE_INSENSITIVE_SOURCES:
        return value.casefold()
    return value


def _identity_reference(identity: str) -> str:
    """Deterministic, non-reversible reference used in structured errors (QA F-9).

    Stable across runs (no random salt) so debugging/tests are reproducible, but
    the raw external identity is never persisted.
    """
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def _normalized_mappings(
    mapping: IdentityMappingConfig,
) -> tuple[dict[str, dict[str, str]], set[tuple[str, str]]]:
    """Return per-source normalized mappings plus ambiguous ``(source, key)`` pairs.

    A normalized key that maps to two different customers cannot be resolved
    safely and is recorded as ambiguous (drop, never guess).
    """
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
    """Result of resolving an external batch.

    ``messages`` contains only safely attached messages (customer-resolved
    customer messages plus agent/system messages that belong to resolved
    threads). ``unresolved`` preserves the dropped customer messages so callers
    can report provenance without ever attaching them.
    """

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
    """Attach ``customer_id`` to customer messages via exact identity lookup.

    Agent/system messages pass through unchanged (they are attached to a thread
    only once the thread owner is known). Customer messages whose identity is
    absent from the mapping are dropped and surfaced as ``UNMAPPED_EXTERNAL_IDENTITY``.
    """
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

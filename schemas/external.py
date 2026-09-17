"""Source-neutral external message contract (Node 3 multi-source ingestion).

External sources (X, Gmail, and future CRM/support/survey systems) normalize
their native payloads into ``ExternalMessage``. A deterministic identity mapping
then binds ``external_identity`` to a canonical ``customer_id`` and the
normalizer groups messages into the existing ``SupportThread`` contract — so
Node 3's preprocessing/extraction/aggregation and every downstream node stay
source-agnostic.

Only fields that are actually needed are modeled. ``metadata`` is an open dict
for source-specific extras; it is never fed to the signal extractor or any model.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ExternalRole = Literal["customer", "agent", "system"]


class ExternalMessage(BaseModel):
    """One normalized message from an external source.

    ``external_identity`` is the source-native author identity (X user id/handle,
    Gmail address, ...). ``customer_id`` stays ``None`` until the deterministic
    identity resolver maps it (architecture §6 of the multi-source addendum).
    ``thread_id``/``message_id`` are the source-native ids; the normalizer
    namespaces them per source for global uniqueness and provenance.

    ``timestamp`` must be timezone-aware; it is normalized to UTC at ingestion so
    timestamps from different offsets compare correctly and naive/aware mixing can
    never raise during ordering. Naive timestamps are rejected explicitly (the
    architecture defines no implicit source timezone — see the multi-source
    addendum §8).
    """

    model_config = ConfigDict(extra="forbid")

    source: str
    source_type: str | None = None
    external_identity: str
    customer_id: str | None = None
    thread_id: str
    message_id: str
    timestamp: datetime
    text: str
    role: ExternalRole = "customer"
    author_id: str | None = None
    conversation_id: str | None = None
    url: str | None = None
    subject: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def _normalize_timestamp_to_utc(cls, value: datetime) -> datetime:
        """Reject naive timestamps; canonicalize aware timestamps to UTC."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "timestamp must be timezone-aware; naive timestamps are rejected "
                "(no implicit source timezone is assumed)"
            )
        return value.astimezone(UTC)

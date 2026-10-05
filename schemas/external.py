from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ExternalRole = Literal["customer", "agent", "system"]


class ExternalMessage(BaseModel):
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
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "timestamp must be timezone-aware; naive timestamps are rejected "
                "(no implicit source timezone is assumed)"
            )
        return value.astimezone(UTC)

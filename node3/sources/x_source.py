from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from node3.sources.base import ExternalSource, build_external_message
from node3.sources.errors import (
    SourceDataError,
    SourceNotConfiguredError,
    SourceNotImplementedError,
)
from schemas.external import ExternalMessage


def _load_list(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceDataError(f"could not read X fixture {path.name}: {exc}") from exc
    if not isinstance(raw, list):
        raise SourceDataError(f"X fixture {path.name} must contain a JSON list")
    entries: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise SourceDataError(f"X fixture {path.name} has a non-object entry")
        entries.append(entry)
    return entries


def _require(entry: dict[str, Any], field: str, *, filename: str) -> Any:
    value = entry.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise SourceDataError(f"X fixture {filename} entry is missing required field {field!r}")
    return value


class XSource(ExternalSource):
    name = "x"

    def __init__(
        self,
        *,
        access_token: str | None,
        include_public: bool = True,
        include_dms: bool = False,
        agent_identities: Sequence[str] = (),
    ) -> None:
        super().__init__(agent_identities=agent_identities)
        self.access_token = access_token
        self.include_public = include_public
        self.include_dms = include_dms

    def fetch_customer_data(
        self, customer_ids: Sequence[str] | None = None
    ) -> list[ExternalMessage]:
        if not self.access_token:
            raise SourceNotConfiguredError(
                "X source is enabled in live mode but X_ACCESS_TOKEN is not set"
            )
        raise SourceNotImplementedError(
            "live X integration is deferred; use mode='mock' or see the multi-source addendum"
        )


class MockXSource(ExternalSource):
    name = "x"

    def __init__(
        self,
        mock_dir: str | Path,
        *,
        include_public: bool = True,
        include_dms: bool = False,
        agent_identities: Sequence[str] = (),
    ) -> None:
        super().__init__(agent_identities=agent_identities)
        self.mock_dir = Path(mock_dir)
        self.include_public = include_public
        self.include_dms = include_dms
        if not self.mock_dir.is_dir():
            raise SourceDataError(f"X mock directory not found: {self.mock_dir}")

    def _parse_public(self, filename: str, source_type: str) -> list[ExternalMessage]:
        path = self.mock_dir / filename
        if not path.exists():
            return []
        messages: list[ExternalMessage] = []
        for index, entry in enumerate(_load_list(path)):
            message_id = str(_require(entry, "id", filename=filename))
            author = str(_require(entry, "author_id", filename=filename))
            messages.append(
                build_external_message(
                    self.name,
                    f"{filename} record {index}",
                    source=self.name,
                    source_type=source_type,
                    external_identity=author,
                    thread_id=str(entry.get("conversation_id") or message_id),
                    message_id=message_id,
                    timestamp=_require(entry, "created_at", filename=filename),
                    text=str(_require(entry, "text", filename=filename)),
                    role="agent" if self.is_agent_identity(author) else "customer",
                    author_id=author,
                    conversation_id=entry.get("conversation_id"),
                    url=entry.get("url"),
                    subject=entry.get("subject"),
                    metadata={
                        "source_type": source_type,
                    },
                )
            )
        return messages

    def _parse_dms(self) -> list[ExternalMessage]:
        path = self.mock_dir / "dms.json"
        if not path.exists():
            return []
        messages: list[ExternalMessage] = []
        for conversation in _load_list(path):
            conversation_id = str(_require(conversation, "conversation_id", filename="dms.json"))
            raw_messages = conversation.get("messages")
            if not isinstance(raw_messages, list):
                raise SourceDataError("X fixture dms.json conversation is missing a messages list")
            for index, entry in enumerate(raw_messages):
                if not isinstance(entry, dict):
                    raise SourceDataError("X fixture dms.json has a non-object message")
                sender = str(_require(entry, "sender_id", filename="dms.json"))
                messages.append(
                    build_external_message(
                        self.name,
                        f"dms.json conversation {conversation_id} message {index}",
                        source=self.name,
                        source_type="dm",
                        external_identity=sender,
                        thread_id=conversation_id,
                        message_id=str(_require(entry, "id", filename="dms.json")),
                        timestamp=_require(entry, "created_at", filename="dms.json"),
                        text=str(_require(entry, "text", filename="dms.json")),
                        role="agent" if self.is_agent_identity(sender) else "customer",
                        author_id=sender,
                        conversation_id=conversation_id,
                        metadata={"source_type": "dm"},
                    )
                )
        return messages

    def fetch_customer_data(
        self, customer_ids: Sequence[str] | None = None
    ) -> list[ExternalMessage]:
        messages: list[ExternalMessage] = []
        if self.include_public:
            messages.extend(self._parse_public("posts.json", "post"))
            messages.extend(self._parse_public("mentions.json", "mention"))
        if self.include_dms:
            messages.extend(self._parse_dms())
        return messages

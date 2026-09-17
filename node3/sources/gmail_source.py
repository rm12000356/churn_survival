"""Gmail source adapter (multi-source addendum §8).

Gmail uses OAuth2 credentials (client id/secret + refresh token) issued out of
band; the repository never stores real credentials. Mock mode is deterministic
and credential-free. The live class validates its OAuth configuration and defers
the concrete Google API transport (documented in the multi-source addendum).
"""

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
        raise SourceDataError(f"could not read Gmail fixture {path.name}: {exc}") from exc
    if not isinstance(raw, list):
        raise SourceDataError(f"Gmail fixture {path.name} must contain a JSON list")
    entries: list[dict[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise SourceDataError(f"Gmail fixture {path.name} has a non-object entry")
        entries.append(entry)
    return entries


def _require(entry: dict[str, Any], field: str, *, filename: str) -> Any:
    value = entry.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise SourceDataError(f"Gmail fixture {filename} entry is missing required field {field!r}")
    return value


class GmailSource(ExternalSource):
    """Live Gmail API source (OAuth2 transport deferred; config validated loudly)."""

    name = "gmail"

    def __init__(
        self,
        *,
        client_id: str | None,
        client_secret: str | None,
        refresh_token: str | None,
        agent_identities: Sequence[str] = (),
    ) -> None:
        super().__init__(agent_identities=agent_identities)
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token

    def fetch_customer_data(
        self, customer_ids: Sequence[str] | None = None
    ) -> list[ExternalMessage]:
        missing = [
            name
            for name, value in (
                ("GMAIL_CLIENT_ID", self.client_id),
                ("GMAIL_CLIENT_SECRET", self.client_secret),
                ("GMAIL_REFRESH_TOKEN", self.refresh_token),
            )
            if not value
        ]
        if missing:
            raise SourceNotConfiguredError(
                "Gmail source is enabled in live mode but missing: " + ", ".join(missing)
            )
        raise SourceNotImplementedError(
            "live Gmail integration is deferred; use mode='mock' or see the "
            "multi-source addendum"
        )


class MockGmailSource(ExternalSource):
    """Deterministic, credential-free Gmail source backed by local fixtures."""

    name = "gmail"

    def __init__(
        self,
        mock_dir: str | Path,
        *,
        agent_identities: Sequence[str] = (),
    ) -> None:
        super().__init__(agent_identities=agent_identities)
        self.mock_dir = Path(mock_dir)
        if not self.mock_dir.is_dir():
            raise SourceDataError(f"Gmail mock directory not found: {self.mock_dir}")

    def _subjects(self) -> dict[str, str]:
        path = self.mock_dir / "threads.json"
        if not path.exists():
            return {}
        subjects: dict[str, str] = {}
        for entry in _load_list(path):
            thread_id = str(_require(entry, "id", filename="threads.json"))
            subject = entry.get("subject")
            if subject:
                subjects[thread_id] = str(subject)
        return subjects

    def fetch_customer_data(
        self, customer_ids: Sequence[str] | None = None
    ) -> list[ExternalMessage]:
        path = self.mock_dir / "messages.json"
        if not path.exists():
            return []
        subjects = self._subjects()
        messages: list[ExternalMessage] = []
        for index, entry in enumerate(_load_list(path)):
            thread_id = str(_require(entry, "thread_id", filename="messages.json"))
            sender = str(_require(entry, "from", filename="messages.json")).strip().lower()
            recipient = entry.get("to")
            messages.append(
                build_external_message(
                    self.name,
                    f"messages.json record {index}",
                    source=self.name,
                    source_type="email",
                    external_identity=sender,
                    thread_id=thread_id,
                    message_id=str(_require(entry, "id", filename="messages.json")),
                    timestamp=_require(entry, "timestamp", filename="messages.json"),
                    text=str(_require(entry, "body", filename="messages.json")),
                    role="agent" if self.is_agent_identity(sender) else "customer",
                    author_id=sender,
                    conversation_id=thread_id,
                    subject=subjects.get(thread_id),
                    metadata={
                        "to": recipient,
                        "snippet": entry.get("snippet"),
                    },
                )
            )
        return messages

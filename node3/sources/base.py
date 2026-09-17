"""External-source abstraction for Node 3 (multi-source addendum §4).

Each source owns its own authentication, API communication, pagination,
rate-limit handling, response parsing, and source-native ids. It returns the
source-neutral ``ExternalMessage`` contract — Node 3's signal extraction never
knows whether input came from mock fixtures or a live API.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from pydantic import ValidationError

from node3.sources.errors import SourceDataError
from schemas.external import ExternalMessage


def build_external_message(
    source_name: str, context: str, /, **fields: object
) -> ExternalMessage:
    """Construct an ``ExternalMessage``, converting validation errors to ``SourceDataError``.

    A malformed source record must never surface as an unhandled Pydantic
    ``ValidationError`` (which would abort every source); it becomes a structured
    source error so ``ingest_external_sources`` can isolate it per source (QA F-4).
    The message names the source and offending field(s) only — never field values,
    credentials, or unnecessary identity/PII.
    """
    try:
        return ExternalMessage(**fields)  # type: ignore[arg-type]
    except ValidationError as exc:
        invalid = sorted({str(error["loc"][-1]) for error in exc.errors()})
        fields_text = ", ".join(invalid) if invalid else "unknown"
        raise SourceDataError(
            f"{source_name} source record ({context}) is malformed: "
            f"invalid field(s): {fields_text}"
        ) from exc


class ExternalSource(ABC):
    """Base class for every external customer-interaction source."""

    name: str = "external"

    def __init__(self, *, agent_identities: Sequence[str] = ()) -> None:
        self.agent_identities: frozenset[str] = frozenset(
            identity.strip().casefold() for identity in agent_identities if identity.strip()
        )

    def is_agent_identity(self, identity: str) -> bool:
        """True when ``identity`` is a declared company/agent identity."""
        return identity.strip().casefold() in self.agent_identities

    @abstractmethod
    def fetch_customer_data(
        self, customer_ids: Sequence[str] | None = None
    ) -> list[ExternalMessage]:
        """Return normalized messages.

        ``customer_ids`` optionally restricts the fetch to the requested
        customers. Sources that cannot filter server-side may ignore it and let
        the identity resolver discard out-of-universe messages deterministically.
        """

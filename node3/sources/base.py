from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from pydantic import ValidationError

from node3.sources.errors import SourceDataError
from schemas.external import ExternalMessage


def build_external_message(
    source_name: str, context: str, /, **fields: object
) -> ExternalMessage:
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
    name: str = "external"

    def __init__(self, *, agent_identities: Sequence[str] = ()) -> None:
        self.agent_identities: frozenset[str] = frozenset(
            identity.strip().casefold() for identity in agent_identities if identity.strip()
        )

    def is_agent_identity(self, identity: str) -> bool:
        return identity.strip().casefold() in self.agent_identities

    @abstractmethod
    def fetch_customer_data(
        self, customer_ids: Sequence[str] | None = None
    ) -> list[ExternalMessage]:
        ...

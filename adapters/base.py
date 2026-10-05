from __future__ import annotations

import abc
from datetime import date
from typing import Any, Protocol

from adapters.tenure import compute_tenure, observation_end_for
from adapters.util import parse_date

RawData = Any
Fingerprint = Any


class Adapter(Protocol):
    name: str
    version: str

    def can_handle(self, sample: Any) -> bool:
        ...

    def transform(self, raw_data: Any, reference_date: str) -> list[dict]:
        ...

    def get_mapping_config(self) -> dict:
        ...


class BaseAdapter(abc.ABC):
    name: str
    version: str

    confidence: float = 1.0

    priority: int = 100

    def matches_signature(self, fingerprint: Fingerprint) -> bool:
        raise NotImplementedError

    def can_handle(self, sample: Any) -> bool:
        from router.fingerprint import extract_fingerprint

        return self.matches_signature(extract_fingerprint(sample))

    @abc.abstractmethod
    def transform(self, raw_data: Any, reference_date: str) -> list[dict]:
        ...

    def get_mapping_config(self) -> dict:
        return {
            "adapter": self.name,
            "adapter_version": self.version,
            "mapping_version": f"{self.name}_v{self.version}",
        }

    @staticmethod
    def _normalize_reference_date(reference_date: str) -> date:
        return date.fromisoformat(reference_date)

    @staticmethod
    def split_features(
        row: dict[str, Any], approved_core_keys: list[str]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        core_features: dict[str, Any] = {key: row.get(key) for key in approved_core_keys}
        extra_features = {key: value for key, value in row.items() if key not in approved_core_keys}
        return core_features, extra_features

    def build_record(
        self,
        *,
        customer_id: str | None,
        observation_start: Any,
        observation_end_raw: Any,
        event_observed: int | None,
        core_features: dict[str, Any],
        extra_features: dict[str, Any],
        reference_date: str,
        original_row_id: str | None = None,
    ) -> dict[str, Any]:
        ref_date = self._normalize_reference_date(reference_date)
        start = parse_date(observation_start)
        end = observation_end_for(event_observed, observation_end_raw, ref_date)
        tenure = compute_tenure(start, end) if start is not None and end is not None else None
        return {
            "customer_id": str(customer_id).strip() if customer_id is not None else None,
            "observation_start": start.isoformat() if start is not None else None,
            "observation_end": end.isoformat() if end is not None else None,
            "event_observed": event_observed,
            "tenure": tenure,
            "core_features": dict(core_features),
            "extra_features": dict(extra_features),
            "meta": {
                "source_adapter": self.name,
                "mapping_version": f"{self.name}_v{self.version}",
                "ingested_at": f"{reference_date}T00:00:00Z",
                "original_row_id": original_row_id,
                "reference_date": reference_date,
            },
        }

"""Adapter interface contract (architecture §1.4, ROADMAP Task 2.1).

`Adapter` is the structural Protocol the architecture mandates. `BaseAdapter`
provides the shared machinery: signature matching against a fingerprint
(§0.1), a declared confidence and priority for the router, and a default
`can_handle(sample)` that delegates to signature matching.

Rules every adapter must obey:
- `transform` returns canonical *record dicts* and never emits non-canonical
  top-level fields or temporary fields like `tenure_start_date`.
- Tenure is computed against the declared `reference_date`, never "today".
- `get_mapping_config()` returns the exact mapping used, for audit.
"""

from __future__ import annotations

import abc
from datetime import date
from typing import Any, Protocol

from adapters.tenure import compute_tenure, observation_end_for
from adapters.util import parse_date

RawData = Any  # DataFrame | dict[str, DataFrame] — kept Any for the Protocol
Fingerprint = Any  # SourceFingerprint (schema object) — kept Any for the Protocol


class Adapter(Protocol):
    """Exact protocol from architecture §1.4."""

    name: str
    version: str

    def can_handle(self, sample: Any) -> bool:
        """Return True only if this adapter is confident it can process the data."""

    def transform(self, raw_data: Any, reference_date: str) -> list[dict]:
        """Transform raw data into canonical record dicts. Never emit non-canonical fields."""

    def get_mapping_config(self) -> dict:
        """Return the exact mapping configuration that was used (for audit)."""


class BaseAdapter(abc.ABC):
    """Shared deterministic-adapter machinery (architecture §1.4/§1.5, §0.1)."""

    name: str
    version: str

    # How confident this adapter is when its signature matches (§0.1). The
    # router only chooses adapters at/above `router_high_confidence_threshold`.
    confidence: float = 1.0

    # Lower number wins on confidence ties (declaration order).
    priority: int = 100

    def matches_signature(self, fingerprint: Fingerprint) -> bool:
        """Return True if the extracted fingerprint matches this adapter's declared shape."""
        raise NotImplementedError

    def can_handle(self, sample: Any) -> bool:
        """Default protocol implementation: extract a fingerprint and test it."""
        from router.fingerprint import extract_fingerprint

        return self.matches_signature(extract_fingerprint(sample))

    @abc.abstractmethod
    def transform(self, raw_data: Any, reference_date: str) -> list[dict]:
        """Transform raw data into canonical record dicts (architecture §1.3)."""

    def get_mapping_config(self) -> dict:
        """Return the mapping configuration used, for audit (§1.4)."""
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
        """Split raw field values into (core_features, extra_features).

        Every approved core key is represented (value may be ``None`` when the
        source lacks it, so batch missingness is computable); all other keys
        land in ``extra_features`` (storage only, never auto-promoted).
        """
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
        """Assemble a canonical record dict (architecture §1.3).

        Applies the censoring rule (active -> reference_date), computes tenure
        deterministically, and never emits temporary/non-canonical fields.
        """
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
                # Deterministic placeholder (reference-date midnight); run_node1 stamps
                # the run timestamp. Never wall-clock (hard rule 1).
                "ingested_at": f"{reference_date}T00:00:00Z",
                "original_row_id": original_row_id,
                "reference_date": reference_date,
            },
        }

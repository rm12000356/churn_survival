"""Run identity computation (ROADMAP Phase 8, D-P1).

A run's identity must track **everything that can change its output** — raw
bytes, support inputs, every versioned config, the declared ``reference_date``,
and the resolved routing decision (which adapter transforms the raw bytes). The
mapping registry is input configuration, so it is part of the identity: after a
mapping is confirmed the identity changes and a fresh run is produced.

This module is **decision-free**: it imports only the standard library and the
Pydantic contracts. It never imports or calls ``node1``..``node5``. The routing
pass that supplies ``routing_identity`` (fingerprint + ``router.route``) is also
decision-free; neither computes a risk level, score, rank, or confidence.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from schemas.run import RoutingIdentity

__all__ = [
    "compute_run_id",
    "compute_support_digest",
    "sha256_file",
]


def sha256_file(path: str | Path) -> str:
    """Return the sha256 hex digest of a file's bytes."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical(value: Any) -> Any:
    """Canonical, JSON-serializable view of a value (deterministic key order)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {
            str(key): _canonical(inner)
            for key, inner in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return str(value)


def _dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_support_digest(
    *,
    support_data: Sequence[Any] | None = None,
    external_threads: Sequence[Any] | None = None,
    sources_config_version: str | None = None,
    identity_mapping_version: str | None = None,
) -> str:
    """Digest of the Node 3 support inputs (threads + source/identity configs)."""
    payload = {
        "support_data": _canonical(list(support_data)) if support_data else None,
        "external_threads": _canonical(list(external_threads)) if external_threads else None,
        "sources_config_version": sources_config_version,
        "identity_mapping_version": identity_mapping_version,
    }
    return hashlib.sha256(_dumps(payload).encode("utf-8")).hexdigest()


def compute_run_id(
    *,
    raw_digest: str,
    support_digest: str,
    config_versions: Mapping[str, str],
    reference_date: date,
    routing_identity: RoutingIdentity | None,
) -> str:
    """Content-addressed run id over the full computation identity (D-P1)."""
    payload = {
        "raw_digest": raw_digest,
        "support_digest": support_digest,
        "config_versions": {str(key): str(value) for key, value in config_versions.items()},
        "reference_date": reference_date.isoformat(),
        "routing_identity": (
            routing_identity.model_dump(mode="json")
            if routing_identity is not None
            else None
        ),
    }
    return hashlib.sha256(_dumps(payload).encode("utf-8")).hexdigest()[:16]

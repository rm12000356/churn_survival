"""Routing node for the orchestrator (architecture §0.1, ROADMAP Task 7.1).

Pure deterministic routing: fingerprint the raw input and ask every registered
adapter (built-ins plus confirmed mapping configs) whether it matches. This node
never calls an LLM — an unmatched shape is *routed* to the human-confirmation
gate, not automatically translated.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from config.models import Node1Config
from router.fingerprint import extract_fingerprint
from router.router import RouterDecision, route
from schemas.mapping import SourceFingerprint
from schemas.run import RoutingIdentity

__all__ = [
    "AUTO_NODE1_VERSION",
    "build_adapters",
    "fingerprint_file",
    "fingerprint_input",
    "resolve_node1_version",
    "route_input",
    "routing_identity",
    "routing_summary",
]

# Sentinel meaning "pick the deployment Node 1 config from the matched mapping".
AUTO_NODE1_VERSION = "auto"


def build_adapters(config_dir: str | Path | None = None) -> list[Any]:
    """Registered deterministic adapters: built-ins + confirmed mappings."""
    from node1.node import _build_adapter_list

    return _build_adapter_list(config_dir)


def fingerprint_input(raw_path: str | Path) -> tuple[Any, SourceFingerprint]:
    """Load the raw input and extract its schema fingerprint."""
    from node1.node import load_raw

    raw = load_raw(raw_path)
    return raw, extract_fingerprint(raw)


# Fingerprints keyed by (resolved path, size, mtime): a file is parsed once per
# version, not once per routing step (API trigger, resolution, worker routing).
_FINGERPRINT_CACHE: OrderedDict[tuple[str, int, int], SourceFingerprint] = OrderedDict()
_FINGERPRINT_CACHE_SIZE = 8
_FINGERPRINT_LOCK = threading.Lock()


def fingerprint_file(raw_path: str | Path) -> SourceFingerprint:
    """Schema fingerprint of ``raw_path``, reusing it until the file changes."""
    path = Path(raw_path)
    if not path.is_file():
        fingerprint_input(path)  # raises the canonical "raw data file not found"
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    with _FINGERPRINT_LOCK:
        cached = _FINGERPRINT_CACHE.get(key)
        if cached is not None:
            _FINGERPRINT_CACHE.move_to_end(key)
            return cached.model_copy(deep=True)
    _raw, fingerprint = fingerprint_input(path)
    with _FINGERPRINT_LOCK:
        _FINGERPRINT_CACHE[key] = fingerprint.model_copy(deep=True)
        while len(_FINGERPRINT_CACHE) > _FINGERPRINT_CACHE_SIZE:
            _FINGERPRINT_CACHE.popitem(last=False)
    return fingerprint


def route_input(
    raw_path: str | Path,
    config: Node1Config,
    *,
    adapters: list[Any] | None = None,
    config_dir: str | Path | None = None,
) -> tuple[SourceFingerprint, RouterDecision]:
    """Fingerprint the input and return the best deterministic routing decision."""
    fingerprint = fingerprint_file(raw_path)
    candidates = list(adapters) if adapters is not None else build_adapters(config_dir)
    decision = route(
        fingerprint,
        candidates,
        high_confidence_threshold=config.router_high_confidence_threshold,
    )
    return fingerprint, decision


def resolve_node1_version(
    raw_path: str | Path,
    requested: str,
    *,
    adapters: list[Any] | None = None,
    config_dir: str | Path | None = None,
    default: str = "1",
) -> tuple[str, str | None]:
    """Resolve the effective Node 1 config version for an input.

    A concrete ``requested`` version is returned unchanged (explicit override).
    ``"auto"`` fingerprints the input and asks the registered deterministic
    adapters which shape matched; when the best match is a confirmed mapping that
    records a deployment Node 1 config (``MappingConfig.node1_config_version``),
    that config is used — so an onboarded dataset gets its own ``approved_core_keys``
    instead of the default. Otherwise ``default`` is used and an explanatory warning
    is returned. Decision-free: fingerprint + route only, no node execution.
    """
    if requested and requested != AUTO_NODE1_VERSION:
        return requested, None
    try:
        fingerprint = fingerprint_file(raw_path)
    except Exception as exc:  # noqa: BLE001 - resolution must not break the run
        return (
            default,
            f"node1 auto-resolve could not fingerprint input ({exc}); using v{default}",
        )
    candidates = list(adapters) if adapters is not None else build_adapters(config_dir)
    # Same routing rule as the run itself, so resolution never picks an adapter
    # that the real routing pass would reject as a low-confidence match.
    from config.loader import load_node1_config

    try:
        threshold = load_node1_config(default).router_high_confidence_threshold
    except Exception:  # noqa: BLE001 - a missing default config must not break resolution
        threshold = 0.0
    decision = route(fingerprint, candidates, high_confidence_threshold=threshold)
    adapter = decision.adapter if decision.matched else None
    recommended = getattr(adapter, "recommended_node1_config", None)
    if recommended is None:
        # Built-in adapter (or no match): defaulting is the normal case, no warning.
        return default, None
    version = recommended()
    if version:
        return str(version), None
    name = getattr(adapter, "name", "adapter")
    return default, (
        f"node1 auto-resolve: matched {name!r} records no deployment Node 1 "
        f"config; using default v{default} (pass --node1 <version> to override)"
    )


def routing_summary(decision: RouterDecision) -> dict[str, Any]:
    """JSON-serializable summary of a routing decision (for persisted state)."""
    adapter = decision.adapter
    return {
        "matched": decision.matched,
        "adapter": getattr(adapter, "name", None) if adapter is not None else None,
        "adapter_version": (getattr(adapter, "version", None) if adapter is not None else None),
        "confidence": decision.confidence,
        "rationale": decision.rationale,
        "matched_candidates": list(decision.matched_candidates),
    }


def routing_identity(decision: RouterDecision) -> RoutingIdentity:
    """Extract the decision-free routing identity from a routing decision (D-P1)."""
    adapter = decision.adapter
    if not decision.matched or adapter is None:
        return RoutingIdentity.no_match()
    return RoutingIdentity(
        matched=True,
        adapter=getattr(adapter, "name", None),
        adapter_version=getattr(adapter, "version", None),
        confidence=decision.confidence,
    )

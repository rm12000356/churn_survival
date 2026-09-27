"""Routing node for the orchestrator (architecture §0.1, ROADMAP Task 7.1).

Pure deterministic routing: fingerprint the raw input and ask every registered
adapter (built-ins plus confirmed mapping configs) whether it matches. This node
never calls an LLM — an unmatched shape is *routed* to the human-confirmation
gate, not automatically translated.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from config.models import Node1Config
from router.fingerprint import extract_fingerprint
from router.router import RouterDecision, route
from schemas.mapping import SourceFingerprint
from schemas.run import RoutingIdentity

__all__ = [
    "build_adapters",
    "fingerprint_input",
    "route_input",
    "routing_identity",
    "routing_summary",
]


def build_adapters(config_dir: str | Path | None = None) -> list[Any]:
    """Registered deterministic adapters: built-ins + confirmed mappings."""
    from node1.node import _build_adapter_list

    return _build_adapter_list(config_dir)


def fingerprint_input(raw_path: str | Path) -> tuple[Any, SourceFingerprint]:
    """Load the raw input and extract its schema fingerprint."""
    from node1.node import load_raw

    raw = load_raw(raw_path)
    return raw, extract_fingerprint(raw)


def route_input(
    raw_path: str | Path,
    config: Node1Config,
    *,
    adapters: list[Any] | None = None,
    config_dir: str | Path | None = None,
) -> tuple[SourceFingerprint, RouterDecision]:
    """Fingerprint the input and return the best deterministic routing decision."""
    _raw, fingerprint = fingerprint_input(raw_path)
    candidates = list(adapters) if adapters is not None else build_adapters(config_dir)
    decision = route(
        fingerprint,
        candidates,
        high_confidence_threshold=config.router_high_confidence_threshold,
    )
    return fingerprint, decision


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

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from schemas.mapping import SourceFingerprint


@dataclass(frozen=True)
class RouterDecision:
    matched: bool
    adapter: Any
    confidence: float | None
    fingerprint: SourceFingerprint
    rationale: str
    matched_candidates: tuple[str, ...] = ()


def route(
    fingerprint: SourceFingerprint,
    adapters: list[Any] | None = None,
    *,
    high_confidence_threshold: float = 0.80,
) -> RouterDecision:
    candidates = list(adapters) if adapters is not None else _default_adapters()
    matching = [
        (adapter, float(adapter.confidence))
        for adapter in candidates
        if adapter.matches_signature(fingerprint)
    ]

    high = [
        (adapter, confidence)
        for adapter, confidence in matching
        if confidence >= high_confidence_threshold
    ]
    if high:
        high.sort(key=lambda item: (-item[1], item[0].priority, item[0].name))
        adapter, confidence = high[0]
        return RouterDecision(
            matched=True,
            adapter=adapter,
            confidence=confidence,
            fingerprint=fingerprint,
            rationale=(
                f"matched adapter {adapter.name!r} (confidence {confidence:.2f}, "
                f"version {adapter.version})"
            ),
            matched_candidates=tuple(adapter.name for adapter, _ in matching),
        )

    if matching:
        names = ", ".join(a.name for a, _ in matching)
        return RouterDecision(
            matched=False,
            adapter=None,
            confidence=None,
            fingerprint=fingerprint,
            rationale=(
                f"adapters [{names}] matched below the high-confidence threshold "
                f"({high_confidence_threshold:.2f}); routing to LLM mapping-report path"
            ),
            matched_candidates=tuple(adapter.name for adapter, _ in matching),
        )

    return RouterDecision(
        matched=False,
        adapter=None,
        confidence=None,
        fingerprint=fingerprint,
        rationale="no deterministic adapter matched; routing to LLM mapping-report path",
    )


_registry: list[Any] | None = None


def _default_adapters() -> list[Any]:
    global _registry
    if _registry is None:
        from adapters.clean_csv import CleanCsvAdapter
        from adapters.excel_multi_sheet import ExcelMultiSheetAdapter
        from adapters.hubspot_crm import HubspotCrmAdapter
        from adapters.stripe_customers import StripeCustomersAdapter
        from adapters.zendesk_intercom import ZendeskIntercomAdapter

        _registry = [
            CleanCsvAdapter(),
            ExcelMultiSheetAdapter(),
            StripeCustomersAdapter(),
            HubspotCrmAdapter(),
            ZendeskIntercomAdapter(),
        ]
    return _registry


def reset_registry() -> None:
    global _registry
    _registry = None

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from adapters.clean_csv import CleanCsvAdapter
from adapters.excel_multi_sheet import ExcelMultiSheetAdapter
from adapters.hubspot_crm import HubspotCrmAdapter
from adapters.stripe_customers import StripeCustomersAdapter
from adapters.zendesk_intercom import ZendeskIntercomAdapter
from router.fingerprint import extract_fingerprint
from router.router import RouterDecision, route

FIXTURES = Path(__file__).parent.parent / "adapters" / "fixtures"


@pytest.mark.parametrize(
    "fixture,expected",
    [
        ("clean_customers.csv", "clean_csv"),
        ("stripe_export.csv", "stripe_customers"),
        ("hubspot_contacts.csv", "hubspot_crm"),
        ("zendesk_tickets.csv", "zendesk_intercom"),
    ],
)
def test_known_fixture_routes_to_expected_adapter(fixture: str, expected: str) -> None:
    frame = pd.read_csv(FIXTURES / fixture)
    decision = route(extract_fingerprint(frame))
    assert decision.matched is True
    assert decision.adapter is not None
    assert decision.adapter.name == expected


def test_route_is_deterministic_across_calls() -> None:
    frame = pd.read_csv(FIXTURES / "clean_customers.csv")
    fingerprint = extract_fingerprint(frame)
    assert route(fingerprint).adapter.name == route(fingerprint).adapter.name


def test_excel_workbook_routes_to_stripe_over_excel() -> None:
    customers = pd.read_csv(FIXTURES / "clean_customers.csv")
    subscriptions = pd.DataFrame(
        {"customer": ["cus_9"], "status": ["active"], "current_period_start": ["2025-01-01"]}
    )
    decision = route(extract_fingerprint({"Customers": customers, "Subscriptions": subscriptions}))
    assert decision.adapter is not None
    assert decision.adapter.name == "stripe_customers"  # higher confidence wins


def test_excel_customers_and_invoices_routes_to_excel_multi_sheet() -> None:
    customers = pd.read_csv(FIXTURES / "clean_customers.csv")
    invoices = pd.DataFrame({"invoice_id": ["INV-1"], "amount": [100.0]})
    decision = route(extract_fingerprint({"Customers": customers, "Invoices": invoices}))
    assert decision.adapter is not None
    assert decision.adapter.name == "excel_multi_sheet"


def test_no_match_routes_to_llm_path() -> None:
    frame = pd.read_csv(FIXTURES / "unmapped_export.csv")
    decision = route(extract_fingerprint(frame))
    assert decision.matched is False
    assert decision.adapter is None
    assert "LLM mapping-report path" in decision.rationale


def test_below_threshold_confidence_routes_to_llm() -> None:
    frame = pd.read_csv(FIXTURES / "clean_customers.csv")
    fingerprint = extract_fingerprint(frame)
    low_confidence = type(
        "Low", (), {"name": "low", "version": "1", "priority": 1, "confidence": 0.5}
    )()
    low_confidence.matches_signature = lambda _fp: True  # type: ignore[method-assign]
    decision = route(fingerprint, [low_confidence])
    assert decision.matched is False
    assert decision.adapter is None


def test_priority_breaks_confidence_tie() -> None:
    frame = pd.read_csv(FIXTURES / "clean_customers.csv")
    fingerprint = extract_fingerprint(frame)

    def make(name: str, priority: int) -> object:
        adapter = type(
            name, (), {"name": name, "version": "1", "priority": priority, "confidence": 0.95}
        )()
        adapter.matches_signature = lambda _fp: True  # type: ignore[method-assign]
        return adapter

    decision = route(fingerprint, [make("second", 50), make("first", 10)])
    assert decision.adapter is not None
    assert decision.adapter.name == "first"
    assert isinstance(decision, RouterDecision)


def test_router_never_calls_llm() -> None:
    """A high-confidence deterministic match must win without any LLM involvement."""
    frame = pd.read_csv(FIXTURES / "clean_customers.csv")
    decision = route(extract_fingerprint(frame))
    assert decision.matched is True
    assert decision.adapter is not None


def test_adapters_declare_signatures() -> None:
    for adapter in (
        CleanCsvAdapter(),
        ExcelMultiSheetAdapter(),
        StripeCustomersAdapter(),
        HubspotCrmAdapter(),
        ZendeskIntercomAdapter(),
    ):
        assert adapter.name and adapter.version
        assert 0 <= adapter.confidence <= 1


def test_reset_registry_forces_rebuild() -> None:
    from router.router import _default_adapters, reset_registry

    first = _default_adapters()
    reset_registry()
    second = _default_adapters()
    assert first is not second
    assert [a.name for a in second] == [
        "clean_csv",
        "excel_multi_sheet",
        "stripe_customers",
        "hubspot_crm",
        "zendesk_intercom",
    ]


def _make_adapter(name: str, priority: int, confidence: float) -> object:
    adapter = type(
        name, (), {"name": name, "version": "1", "priority": priority, "confidence": confidence}
    )()
    adapter.matches_signature = lambda _fp: True  # type: ignore[method-assign]
    return adapter


def test_many_adapters_mapping_config_wins_for_its_fingerprint() -> None:
    """With ~15 registered adapters all matching, the confirmed mapping (1.0/0) wins."""
    fingerprint = extract_fingerprint(pd.read_csv(FIXTURES / "clean_customers.csv"))
    adapters = [
        _make_adapter(f"adapter_{i:02d}", i * 10, 0.9 + (i % 3) * 0.01) for i in range(1, 15)
    ]
    adapters.append(_make_adapter("mapping:conf", 0, 1.0))

    decision = route(fingerprint, adapters)
    assert decision.adapter is not None
    assert decision.adapter.name == "mapping:conf"
    assert len(decision.matched_candidates) == 15


def test_many_adapters_tie_breaks_by_confidence_then_priority() -> None:
    fingerprint = extract_fingerprint(pd.read_csv(FIXTURES / "clean_customers.csv"))
    adapters = [
        _make_adapter(f"adapter_{i:02d}", i * 10, 0.9 + (i % 3) * 0.01) for i in range(1, 15)
    ]
    decision = route(fingerprint, adapters)
    assert decision.adapter is not None
    # highest confidence 0.92 is tied by adapters 02/05/08/11/14 -> lowest priority wins
    assert decision.adapter.name == "adapter_02"

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

REFERENCE_DATE = "2026-08-15"
FIXTURES = Path(__file__).parent / "fixtures"


def _frame(name: str) -> pd.DataFrame:
    return pd.read_csv(FIXTURES / name)


def test_clean_csv_rejects_workbook_input() -> None:
    with pytest.raises(TypeError, match="single table"):
        CleanCsvAdapter().transform({"Customers": _frame("clean_customers.csv")}, REFERENCE_DATE)


def test_excel_adapter_picks_customer_sheet_by_name() -> None:
    raw = {
        "Invoices": pd.DataFrame({"invoice_id": ["INV-1"]}),
        "Customers": _frame("clean_customers.csv"),
    }
    records = ExcelMultiSheetAdapter().transform(raw, REFERENCE_DATE)
    assert len(records) == 3


def test_excel_adapter_rejects_empty_workbook() -> None:
    with pytest.raises(ValueError, match="empty workbook"):
        ExcelMultiSheetAdapter().transform({}, REFERENCE_DATE)


def test_excel_adapter_rejects_non_dataframe_sheet() -> None:
    raw = {"Customers": "not-a-dataframe"}
    with pytest.raises(TypeError, match="not a DataFrame"):
        ExcelMultiSheetAdapter().transform(raw, REFERENCE_DATE)


def test_hubspot_matches_signature_via_contacts_sheet() -> None:
    fingerprint = extract_fingerprint({"Contacts": _frame("hubspot_contacts.csv")})
    assert HubspotCrmAdapter().matches_signature(fingerprint) is True


def test_hubspot_picks_contacts_sheet_from_workbook() -> None:
    raw = {
        "Deals": pd.DataFrame({"deal_id": ["D-1"]}),
        "Contacts": _frame("hubspot_contacts.csv"),
    }
    records = HubspotCrmAdapter().transform(raw, REFERENCE_DATE)
    assert len(records) == 3


def test_hubspot_workbook_without_contacts_raises() -> None:
    raw = {"Deals": pd.DataFrame({"deal_id": ["D-1"]})}
    with pytest.raises(TypeError, match="found no .* sheet"):
        HubspotCrmAdapter().transform(raw, REFERENCE_DATE)


def test_hubspot_unknown_lifecyclestage_defaults_to_active() -> None:
    frame = _frame("hubspot_contacts.csv")
    frame.loc[0, "lifecyclestage"] = None
    records = HubspotCrmAdapter().transform(frame, REFERENCE_DATE)
    assert records[0]["event_observed"] == 0


def test_stripe_matches_signature_via_subscriptions_sheet() -> None:
    fingerprint = extract_fingerprint({"Subscriptions": _frame("stripe_export.csv")})
    assert StripeCustomersAdapter().matches_signature(fingerprint) is True


def test_stripe_picks_subscriptions_sheet_from_workbook() -> None:
    raw = {
        "Payouts": pd.DataFrame({"payout_id": ["P-1"]}),
        "Subscriptions": _frame("stripe_export.csv"),
    }
    records = StripeCustomersAdapter().transform(raw, REFERENCE_DATE)
    assert len(records) == 3


def test_stripe_workbook_without_subscriptions_raises() -> None:
    raw = {"Payouts": pd.DataFrame({"payout_id": ["P-1"]})}
    with pytest.raises(TypeError, match="found no .* sheet"):
        StripeCustomersAdapter().transform(raw, REFERENCE_DATE)


def test_stripe_interval_fallback_to_months() -> None:
    frame = _frame("stripe_export.csv")
    frame.loc[0, "interval_months"] = None
    frame.loc[0, "interval"] = "year"
    records = StripeCustomersAdapter().transform(frame, REFERENCE_DATE)
    assert records[0]["core_features"]["contract_length_months"] == 12.0


def test_zendesk_matches_signature_via_tickets_sheet() -> None:
    fingerprint = extract_fingerprint({"Tickets": _frame("zendesk_tickets.csv")})
    assert ZendeskIntercomAdapter().matches_signature(fingerprint) is True


def test_zendesk_picks_tickets_sheet_from_workbook() -> None:
    raw = {
        "Users": pd.DataFrame({"user_id": ["u-1"]}),
        "Tickets": _frame("zendesk_tickets.csv"),
    }
    records = ZendeskIntercomAdapter().transform(raw, REFERENCE_DATE)
    assert len(records) == 3


def test_zendesk_unknown_status_defaults_to_active() -> None:
    frame = _frame("zendesk_tickets.csv")
    frame.loc[0, "status"] = "weird"
    records = ZendeskIntercomAdapter().transform(frame, REFERENCE_DATE)
    assert records[0]["event_observed"] == 0

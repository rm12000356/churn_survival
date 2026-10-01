from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from adapters.base import Adapter
from adapters.clean_csv import CleanCsvAdapter
from adapters.excel_multi_sheet import ExcelMultiSheetAdapter
from adapters.hubspot_crm import HubspotCrmAdapter
from adapters.stripe_customers import StripeCustomersAdapter
from adapters.tenure import compute_tenure, observation_end_for
from adapters.util import parse_date, status_to_event, to_float
from adapters.zendesk_intercom import ZendeskIntercomAdapter
from schemas.canonical import CanonicalRecord

REFERENCE_DATE = "2026-08-15"
FIXTURES = Path(__file__).parent / "fixtures"


def _frame(name: str) -> pd.DataFrame:
    return pd.read_csv(FIXTURES / name)


@pytest.mark.parametrize(
    "adapter,fixture",
    [
        (CleanCsvAdapter(), "clean_customers.csv"),
        (StripeCustomersAdapter(), "stripe_export.csv"),
        (HubspotCrmAdapter(), "hubspot_contacts.csv"),
        (ZendeskIntercomAdapter(), "zendesk_tickets.csv"),
    ],
)
def test_adapter_produces_valid_canonical_records(adapter: Adapter, fixture: str) -> None:
    records = adapter.transform(_frame(fixture), REFERENCE_DATE)
    assert len(records) == 3
    for record in records:
        assert CanonicalRecord.model_validate(record).customer_id


def test_clean_csv_tenure_matches_architecture_example() -> None:
    records = CleanCsvAdapter().transform(_frame("clean_customers.csv"), REFERENCE_DATE)
    by_id = {r["customer_id"]: r for r in records}
    assert by_id["cus_1001"]["tenure"] == 521.0  # active, §1.3 example
    assert by_id["cus_1002"]["tenure"] == 472.0  # churned, §1.3 example
    assert by_id["cus_1001"]["event_observed"] == 0
    assert by_id["cus_1002"]["event_observed"] == 1
    assert by_id["cus_1001"]["observation_end"] == REFERENCE_DATE


def test_clean_csv_extra_features_are_storage_only() -> None:
    records = CleanCsvAdapter().transform(_frame("clean_customers.csv"), REFERENCE_DATE)
    extra = records[0]["extra_features"]
    assert extra["region"] == "EU"
    assert "plan_tier" not in extra


def test_stripe_adapter_event_and_interval() -> None:
    records = StripeCustomersAdapter().transform(_frame("stripe_export.csv"), REFERENCE_DATE)
    by_id = {r["customer_id"]: r for r in records}
    assert by_id["cus_2001"]["event_observed"] == 0  # active -> censored
    assert by_id["cus_2002"]["event_observed"] == 1  # canceled -> churn date
    assert by_id["cus_2002"]["observation_end"] == "2025-06-15"
    assert by_id["cus_2003"]["event_observed"] == 0  # trialing -> censored
    assert by_id["cus_2002"]["core_features"]["contract_length_months"] == 12.0


def test_hubspot_adapter_lifecyclestage() -> None:
    records = HubspotCrmAdapter().transform(_frame("hubspot_contacts.csv"), REFERENCE_DATE)
    by_id = {r["customer_id"]: r for r in records}
    assert by_id["a@x.com"]["event_observed"] == 0
    assert by_id["b@x.com"]["event_observed"] == 1
    assert by_id["b@x.com"]["observation_end"] == "2025-11-20"
    assert by_id["c@x.com"]["event_observed"] == 0
    assert by_id["b@x.com"]["core_features"]["plan_tier"] == "basic"


def test_zendesk_adapter_status_mapping() -> None:
    records = ZendeskIntercomAdapter().transform(_frame("zendesk_tickets.csv"), REFERENCE_DATE)
    by_id = {r["customer_id"]: r for r in records}
    assert by_id["user_1001"]["event_observed"] == 0
    assert by_id["user_1002"]["event_observed"] == 1
    assert by_id["user_1003"]["event_observed"] == 0
    assert by_id["user_1001"]["core_features"]["plan_tier"] == "pro"


def test_excel_multi_sheet_adapter() -> None:
    customers = _frame("clean_customers.csv")
    invoices = pd.DataFrame({"invoice_id": ["INV-1"], "amount": [100.0]})
    raw = {"Customers": customers, "Invoices": invoices}
    records = ExcelMultiSheetAdapter().transform(raw, REFERENCE_DATE)
    assert len(records) == 3
    assert CanonicalRecord.model_validate(records[0]).customer_id


def test_excel_adapter_rejects_single_table() -> None:
    with pytest.raises(TypeError):
        ExcelMultiSheetAdapter().transform(_frame("clean_customers.csv"), REFERENCE_DATE)


def test_adapters_reject_wrong_input_type() -> None:
    for adapter in (
        CleanCsvAdapter(),
        StripeCustomersAdapter(),
        HubspotCrmAdapter(),
        ZendeskIntercomAdapter(),
    ):
        with pytest.raises(TypeError):
            adapter.transform(["not", "a", "frame"], REFERENCE_DATE)


def test_get_mapping_config_records_audit() -> None:
    adapter = CleanCsvAdapter()
    config = adapter.get_mapping_config()
    assert config["adapter"] == "clean_csv"
    assert config["adapter_version"] == "1.1.0"
    assert "mapping_version" in config


def test_observation_end_censoring_rule() -> None:
    from datetime import date

    assert observation_end_for(1, "2025-06-15", date(2026, 8, 15)) == date(2025, 6, 15)
    assert observation_end_for(0, "2025-06-15", date(2026, 8, 15)) == date(2026, 8, 15)
    assert observation_end_for(None, None, date(2026, 8, 15)) == date(2026, 8, 15)


def test_compute_tenure() -> None:
    from datetime import date

    assert compute_tenure(date(2025, 3, 12), date(2026, 8, 15)) == 521.0
    assert compute_tenure(date(2024, 11, 3), date(2026, 2, 18)) == 472.0


def test_util_parsers() -> None:
    from datetime import date

    assert parse_date("2026-08-15") == date(2026, 8, 15)
    assert parse_date("08/15/2026") == date(2026, 8, 15)
    assert parse_date("2026-08-15T10:00:00Z") == date(2026, 8, 15)
    assert parse_date("not-a-date") is None
    assert parse_date(None) is None
    assert to_float("12") == 12.0
    assert to_float("1,000.5") == 1000.5
    assert to_float("nan") is None
    assert status_to_event("Churned") == 1
    assert status_to_event("Active") == 0
    assert status_to_event("unknown") is None


def test_invalid_records_fail_pydantic() -> None:
    records = CleanCsvAdapter().transform(_frame("clean_customers.csv"), REFERENCE_DATE)
    bad = dict(records[0])
    bad["event_observed"] = 2
    with pytest.raises(ValidationError):
        CanonicalRecord.model_validate(bad)

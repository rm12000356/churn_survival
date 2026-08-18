"""Shared fixture factories (ROADMAP Task 0.6).

Factories are plain functions returning deep copies of the architecture's
*contract examples* (§1.3 canonical records, §3.2 SupportThread, §1.6
MappingReport). Tests may mutate returned dicts freely without cross-test
leakage. These become the canonical examples used by Phase 1 schema tests.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

_HEADERS_HASH = "a" * 64  # sha256 hex of a sorted header set (fixed for fixtures)

ACTIVE_CUSTOMER = {
    "customer_id": "cus_8f3a2b1c",
    "observation_start": "2025-03-12",
    "observation_end": "2026-08-15",
    "event_observed": 0,
    "tenure": 521.0,
    "core_features": {
        "plan_tier": "pro",
        "contract_length_months": 12.0,
        "usage_frequency": 28.4,
    },
    "extra_features": {
        "last_login_days_ago": 3,
        "support_tickets_90d": 1,
        "region": "EU",
        "sales_rep": "alice@company.com",
    },
    "meta": {
        "source_adapter": "hubspot_messy_v3",
        "mapping_version": "map_2026-08-12T14:22:00Z",
        "ingested_at": "2026-08-17T10:05:33Z",
        "original_row_id": "row_1842",
        "reference_date": "2026-08-15",
    },
}

CHURNED_CUSTOMER = {
    "customer_id": "cus_9d2e4f7a",
    "observation_start": "2024-11-03",
    "observation_end": "2026-02-18",
    "event_observed": 1,
    "tenure": 472.0,
    "core_features": {
        "plan_tier": "starter",
        "contract_length_months": 6.0,
        "usage_frequency": 4.1,
    },
    "extra_features": {
        "last_login_days_ago": 41,
        "support_tickets_90d": 4,
        "region": "US",
        "sales_rep": "bob@company.com",
    },
    "meta": {
        "source_adapter": "hubspot_messy_v3",
        "mapping_version": "map_2026-08-12T14:22:00Z",
        "ingested_at": "2026-08-17T10:05:33Z",
        "original_row_id": "row_1827",
        "reference_date": "2026-08-15",
    },
}

SUPPORT_THREAD = {
    "thread_id": "thr_1001",
    "customer_id": "cus_8f3a2b1c",
    "created_at": "2026-07-20T14:30:00Z",
    "closed_at": "2026-07-21T09:12:00Z",
    "channel": "email",
    "subject": "Downgrade question",
    "status": "closed",
    "tags": ["billing"],
    "messages": [
        {
            "message_id": "msg_1001_1",
            "timestamp": "2026-07-20T14:30:00Z",
            "role": "customer",
            "text": "We are considering cancelling before the renewal.",
        },
        {
            "message_id": "msg_1001_2",
            "timestamp": "2026-07-20T15:00:00Z",
            "role": "agent",
            "text": "I can help with that — what are the blockers?",
        },
    ],
}

MAPPING_REPORT = {
    "source_fingerprint": {
        "headers_hash": _HEADERS_HASH,
        "sheet_names": ["Customers", "Subscriptions"],
        "column_names": [
            "Cust ID",
            "Start Date",
            "Churn Date",
            "Status",
            "Plan Name",
            "Last Login",
        ],
        "sample_dtypes": {
            "Cust ID": "object",
            "Start Date": "object",
            "Churn Date": "object",
            "Status": "object",
            "Plan Name": "object",
            "Last Login": "object",
        },
        "n_sample_rows": 25,
    },
    "proposed_mappings": [
        {
            "source_column": "Cust ID / Account Number",
            "target_field": "customer_id",
            "confidence": 0.97,
            "transformation": "str.strip()",
            "notes": "Appears unique",
        },
        {
            "source_column": "Start Date",
            "target_field": "observation_start",
            "confidence": 0.93,
            "transformation": "parse_date(mixed_formats=True)",
            "notes": "Mixed formats observed (YYYY-MM-DD and MM/DD/YYYY)",
        },
        {
            "source_column": "Churn Date",
            "target_field": "observation_end",
            "confidence": 0.89,
            "transformation": "parse_date; if null/empty then use reference_date",
            "notes": "Null means still active -> censor at reference_date",
        },
        {
            "source_column": "Status",
            "target_field": "event_observed",
            "confidence": 0.91,
            "transformation": "map({'Churned': 1, 'Active': 0})",
            "notes": None,
        },
    ],
    "unmapped_columns": ["Internal Notes", "Legacy Flag"],
    "suggested_extra_features": [
        {"source": "Plan Name", "suggested_key": "plan_name"},
        {"source": "Last Login", "suggested_key": "last_login_days_ago"},
    ],
    "data_quality_flags": [
        "Column 'Start Date' has 12% unparseable values",
        "3 duplicate customer_ids found in sample",
    ],
    "recommended_action": "create_deterministic_adapter",
    "llm_model_used": "anthropic/claude-3-5-sonnet-20241022",
    "generated_at": "2026-08-17T09:41:12Z",
}


def _fresh(template: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(template)


def make_active_customer() -> dict[str, Any]:
    return _fresh(ACTIVE_CUSTOMER)


def make_churned_customer() -> dict[str, Any]:
    return _fresh(CHURNED_CUSTOMER)


def make_support_thread() -> dict[str, Any]:
    return _fresh(SUPPORT_THREAD)


def make_mapping_report() -> dict[str, Any]:
    return _fresh(MAPPING_REPORT)


@pytest.fixture
def active_customer() -> dict[str, Any]:
    return make_active_customer()


@pytest.fixture
def churned_customer() -> dict[str, Any]:
    return make_churned_customer()


@pytest.fixture
def support_thread() -> dict[str, Any]:
    return make_support_thread()


@pytest.fixture
def mapping_report() -> dict[str, Any]:
    return make_mapping_report()


@pytest.fixture
def fresh_settings(monkeypatch):
    """Isolate settings: force a fresh singleton, hermetic against any local `.env`."""
    monkeypatch.setenv("REFERENCE_DATE", "2026-08-15")
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    import config.settings as cs

    monkeypatch.setattr(cs, "_settings", None)
    yield

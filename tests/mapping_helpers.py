"""Shared helpers for LLM mapping-path tests (no real LLM is ever called)."""

from __future__ import annotations

import json


class FakeClient:
    """Stub LLM client returning a fixed string."""

    def __init__(self, text: str) -> None:
        self.text = text

    def complete(self, prompt: str) -> str:
        return self.text


def _mapping(
    source: str,
    target: str,
    confidence: float,
    transformation: str,
    notes: str | None = None,
) -> dict:
    return {
        "source_column": source,
        "target_field": target,
        "confidence": confidence,
        "transformation": transformation,
        "notes": notes,
    }


def mapping_payload(fingerprint) -> dict:
    """A valid MappingReport payload matching ``fingerprint`` (headers = unmapped fixture)."""
    return {
        "source_fingerprint": fingerprint.model_dump(mode="json"),
        "proposed_mappings": [
            _mapping("Cust ID", "customer_id", 0.97, "str.strip()"),
            _mapping("Start Date", "observation_start", 0.95, "parse_date"),
            _mapping(
                "Churn Date",
                "observation_end",
                0.93,
                "parse_date; if null use reference_date",
                "null means active",
            ),
            _mapping("Status", "event_observed", 0.95, "map({'Churned': 1, 'Active': 0})"),
            _mapping("Plan Name", "core.plan_tier", 0.90, "str.strip()"),
            _mapping("Contract Months", "core.contract_length_months", 0.90, "to_float"),
            _mapping("Usage Freq", "core.usage_frequency", 0.90, "to_float"),
        ],
        "unmapped_columns": [],
        "suggested_extra_features": [
            {"source": "Last Login", "suggested_key": "last_login_days_ago"}
        ],
        "data_quality_flags": [],
        "recommended_action": "create_deterministic_adapter",
        "llm_model_used": "test/fake",
        "generated_at": "2026-08-17T09:41:12Z",
    }


def fake_report_text(fingerprint) -> str:
    return json.dumps(mapping_payload(fingerprint))

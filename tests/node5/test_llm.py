"""Milestone B — optional LLM explanation, validation, and deterministic fallback."""

from __future__ import annotations

import json

from node5.llm.explainer import _extract_json, build_prompt, explain_account
from node5.node import run_node5
from tests.node5.conftest import FakeLlmClient, make_sample_inputs, make_sample_node4

VALID = json.dumps(
    {
        "headline": "Critical risk: cancellation intent detected",
        "summary": (
            "The account shows explicit cancellation intent in recent support "
            "interactions."
        ),
        "reason_explanations": ["Cancellation intent was detected."],
    }
)


def _account(customer_id: str = "A"):
    return next(
        a for a in make_sample_node4().ranked_accounts if a.customer_id == customer_id
    )


def test_extract_json_strips_code_fences() -> None:
    assert _extract_json(f"```json\n{VALID}\n```") == VALID


def test_build_prompt_excludes_decision_fields_we_forbid() -> None:
    prompt = build_prompt(_account(), "Acme")
    assert "risk_level" in prompt  # supplied as context
    assert '"recommendation"' not in prompt


def test_valid_llm_explanation_is_used(node5_config, action_rules) -> None:
    node4, node3 = make_sample_inputs()
    client = FakeLlmClient([VALID])
    output = run_node5(
        node4, node5_config, node3_output=node3, action_rules=action_rules, llm_client=client
    )
    account = next(a for a in output.report.priority_accounts if a.customer_id == "A")
    assert account.headline == "Critical risk: cancellation intent detected"
    assert output.processing_report.llm_calls >= 1
    # The same text is rejected for accounts whose facts do not support it.
    assert output.processing_report.llm_failures >= 1
    assert output.metadata.llm_model_version == "fake-model"


def test_invalid_json_falls_back(node5_config, action_rules) -> None:
    node4, node3 = make_sample_inputs()
    client = FakeLlmClient(["not json at all"])
    output = run_node5(
        node4, node5_config, node3_output=node3, action_rules=action_rules, llm_client=client
    )
    account = next(a for a in output.report.priority_accounts if a.customer_id == "A")
    assert account.headline.startswith("Critical")
    assert output.processing_report.llm_failures >= 1
    assert output.report.priority_accounts  # report still complete


def test_timeout_falls_back(node5_config, action_rules) -> None:
    node4, node3 = make_sample_inputs()
    client = FakeLlmClient([TimeoutError("deadline exceeded")])
    output = run_node5(
        node4, node5_config, node3_output=node3, action_rules=action_rules, llm_client=client
    )
    assert output.processing_report.llm_failures >= 1
    assert output.report.priority_accounts


def test_forbidden_decision_field_is_rejected(node5_config, action_rules) -> None:
    node4, node3 = make_sample_inputs()
    bad = json.dumps(
        {
            "headline": "Critical risk: cancellation intent detected",
            "summary": "Explicit cancellation intent in recent support interactions.",
            "reason_explanations": [],
            "risk_level": "critical",
            "rank": 1,
            "score": 0.99,
        }
    )
    client = FakeLlmClient([bad])
    output = run_node5(
        node4, node5_config, node3_output=node3, action_rules=action_rules, llm_client=client
    )
    assert output.processing_report.llm_failures >= 1
    assert output.metadata.llm_model_version == "fake-model"


def test_cancellation_claim_for_billing_account_falls_back(node5_config, action_rules) -> None:
    node4, node3 = make_sample_inputs()
    bad = json.dumps(
        {
            "headline": "High risk",
            "summary": "The customer has indicated that they want to cancel.",
            "reason_explanations": [],
        }
    )

    client = FakeLlmClient([bad])
    output = run_node5(
        node4, node5_config, node3_output=node3, action_rules=action_rules, llm_client=client
    )
    # B's LLM text is rejected; the deterministic template remains.
    account_b = next(a for a in output.report.priority_accounts if a.customer_id == "B")
    assert "cancel" not in account_b.summary.lower()
    assert output.processing_report.llm_failures >= 1


def test_explainer_returns_none_on_exhausted_retries(node5_config) -> None:
    counters = {"llm_calls": 0, "llm_failures": 0}
    warnings: list[str] = []
    client = FakeLlmClient(["garbage"])
    headline, summary = explain_account(
        _account(), "Acme", node5_config, client, counters, warnings
    )
    assert headline is None and summary is None
    assert counters["llm_failures"] == node5_config.llm_max_retries + 1
    assert warnings

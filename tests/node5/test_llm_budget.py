"""REVIEW §5 — Node 5 LLM polish is opt-in, capped, and circuit-broken.

Also covers the validator fixes that made every live explanation fall back:
M-R2 (digit tokens for allowed integers), L25 (the account's own id/name), and
M-R3 (pricing / switching / dissatisfaction language needs a supporting flag).
"""

from __future__ import annotations

import json
import re
import threading
import time

from config.loader import load_node5_config
from node5.node import run_node5
from node5.report.explanation_validator import build_allowed_facts, validate_explanation
from tests.node5.conftest import (
    FakeLlmClient,
    llm_enabled_config,
    make_sample_inputs,
    make_sample_node4,
)

GENERIC = json.dumps(
    {"headline": "Account summary", "summary": "See the listed reasons.", "reason_explanations": []}
)
GARBAGE = "not json"


def _account(customer_id: str):
    return next(
        a for a in make_sample_node4().ranked_accounts if a.customer_id == customer_id
    )


def _run(config, client, action_rules):
    node4, node3 = make_sample_inputs()
    return run_node5(
        node4, config, node3_output=node3, action_rules=action_rules, llm_client=client
    )


# --- opt-in / cap / circuit breaker -------------------------------------------


def test_shipped_configs_keep_llm_polish_off() -> None:
    for version in ("1", "dataset7"):
        config = load_node5_config(version)
        assert config.llm_enabled is False
        assert config.llm_max_retries == 0


def test_client_is_ignored_unless_enabled(node5_config, action_rules) -> None:
    client = FakeLlmClient([GENERIC])
    output = _run(node5_config, client, action_rules)
    assert client.calls == 0
    assert output.processing_report.llm_calls == 0
    assert output.metadata.llm_model_version is None
    assert output.processing_report.explanation_source_summary["llm"] == 0


def test_llm_limited_to_max_accounts(action_rules) -> None:
    config = llm_enabled_config(llm_max_accounts=2, llm_max_retries=0)
    client = FakeLlmClient([GENERIC])
    output = _run(config, client, action_rules)
    assert client.calls == 2
    sources = [a.explanation_source for a in output.report.priority_accounts]
    assert sources == ["llm", "llm", "template", "template"]
    assert any("llm_max_accounts=2" in w for w in output.processing_report.warnings)


def test_circuit_breaker_stops_after_consecutive_failures(action_rules) -> None:
    config = llm_enabled_config(llm_max_consecutive_failures=2, llm_max_retries=0)
    client = FakeLlmClient([GARBAGE])
    output = _run(config, client, action_rules)
    assert client.calls == 2
    assert output.processing_report.llm_failures == 2
    warnings = output.processing_report.warnings
    # Collapsed: one rejection summary + one breaker notice, never one per account.
    assert sum("LLM explanation rejected" in w for w in warnings) == 1
    assert any("stopped after 2 consecutive" in w for w in warnings)


def test_success_resets_the_breaker(action_rules) -> None:
    config = llm_enabled_config(llm_max_consecutive_failures=2, llm_max_retries=0)
    client = FakeLlmClient([GARBAGE, GENERIC, GARBAGE, GENERIC])
    output = _run(config, client, action_rules)
    assert client.calls == 4
    assert output.processing_report.llm_failures == 2


def test_budget_never_changes_decisions(node5_config, action_rules) -> None:
    template = _run(node5_config, None, action_rules)
    polished = _run(llm_enabled_config(), FakeLlmClient([GENERIC]), action_rules)
    for left, right in zip(
        template.report.priority_accounts, polished.report.priority_accounts, strict=True
    ):
        assert (left.customer_id, left.rank, left.risk_level) == (
            right.customer_id,
            right.rank,
            right.risk_level,
        )


# --- concurrency ---------------------------------------------------------------


class ConcurrentClient:
    """Thread-safe double: reply depends only on the prompt; tracks peak concurrency."""

    def __init__(self, fail_ids: frozenset[str] = frozenset(), delay: float = 0.02) -> None:
        self.model = "fake-model"
        self.fail_ids = fail_ids
        self.delay = delay
        self.calls = 0
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()

    def complete(self, prompt: str, *, temperature: float = 0.2) -> str:
        with self._lock:
            self.calls += 1
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(self.delay)
            customer = re.search(r'"customer_id": "([^"]+)"', prompt).group(1)  # type: ignore[union-attr]
            return GARBAGE if customer in self.fail_ids else GENERIC
        finally:
            with self._lock:
                self.active -= 1


def test_concurrent_output_matches_sequential(action_rules) -> None:
    sequential = _run(
        llm_enabled_config(llm_max_concurrency=1), ConcurrentClient(frozenset({"B"})), action_rules
    )
    client = ConcurrentClient(frozenset({"B"}))
    concurrent = _run(llm_enabled_config(llm_max_concurrency=4), client, action_rules)
    assert concurrent.model_dump_json() == sequential.model_dump_json()
    assert client.peak > 1
    assert [a.explanation_source for a in concurrent.report.priority_accounts] == [
        "llm",
        "template",
        "llm",
        "llm",
    ]


def test_concurrency_respects_account_cap(action_rules) -> None:
    client = ConcurrentClient()
    output = _run(
        llm_enabled_config(llm_max_concurrency=8, llm_max_accounts=3, llm_max_retries=0),
        client,
        action_rules,
    )
    assert client.calls == 3
    assert [a.explanation_source for a in output.report.priority_accounts].count("llm") == 3


def test_breaker_is_checked_between_batches(action_rules) -> None:
    client = ConcurrentClient(frozenset({"A", "B", "C", "D"}))
    output = _run(
        llm_enabled_config(
            llm_max_concurrency=2, llm_max_consecutive_failures=2, llm_max_retries=0
        ),
        client,
        action_rules,
    )
    # First batch (A, B) both fail -> breaker opens -> batch (C, D) never submitted.
    assert client.calls == 2
    assert output.processing_report.llm_failures == 2
    assert any("stopped after 2 consecutive" in w for w in output.processing_report.warnings)


# --- validator: M-R2 / L25 / M-R3 ----------------------------------------------


def test_ninety_day_digit_token_is_supported() -> None:
    allowed = build_allowed_facts(_account("A"))
    violations = validate_explanation(
        "Critical risk", "The 90-day outlook reflects cancellation intent.", [], allowed
    )
    assert violations == []


def test_other_digit_tokens_and_percentages_still_rejected() -> None:
    allowed = build_allowed_facts(_account("A"))
    assert "UNSUPPORTED_NUMBER: 37" in validate_explanation(
        "Critical risk", "Seen 37 times.", [], allowed
    )
    assert "UNSUPPORTED_NUMBER: 90%" in validate_explanation(
        "Critical risk", "A 90% figure.", [], allowed
    )


def test_own_customer_id_and_name_are_masked() -> None:
    account = _account("C")  # medium
    allowed = build_allowed_facts(account, display_name="High Street Bakery 42")
    violations = validate_explanation(
        "Medium risk for High Street Bakery 42",
        f"Account {account.customer_id} reported poor support experiences.",
        [],
        allowed,
    )
    assert violations == []


def test_masking_does_not_hide_other_numbers_or_levels() -> None:
    allowed = build_allowed_facts(_account("C"), display_name="Acme 42")
    violations = validate_explanation("Acme 42 is high risk", "Seen 43 times.", [], allowed)
    assert "RISK_LEVEL_MISMATCH: high" in violations
    assert "UNSUPPORTED_NUMBER: 43" in violations


def test_short_display_name_is_masked_as_whole_word_only() -> None:
    allowed = build_allowed_facts(_account("C"), display_name="A")
    violations = validate_explanation("A is high risk", "", [], allowed)
    assert "RISK_LEVEL_MISMATCH: high" in violations


def test_pricing_claim_needs_billing_flag() -> None:
    support_only = build_allowed_facts(_account("C"))  # poor_support_experience
    assert any(
        v.startswith("UNSUPPORTED_RISK_FACTOR")
        for v in validate_explanation("Medium risk", "Pricing concerns.", [], support_only)
    )
    billing = build_allowed_facts(_account("B"))  # billing_complaint
    assert validate_explanation("High risk", "Pricing concerns were raised.", [], billing) == []


def test_switching_language_needs_competitor_flag() -> None:
    allowed = build_allowed_facts(_account("B"))
    violations = validate_explanation(
        "High risk", "They may be switching to another provider.", [], allowed
    )
    assert any(v.startswith("UNSUPPORTED_RISK_FACTOR") for v in violations)


def test_dissatisfaction_needs_some_risk_flag() -> None:
    no_flags = build_allowed_facts(_account("D"))  # low, no flags
    assert any(
        v.startswith("UNSUPPORTED_RISK_FACTOR")
        for v in validate_explanation("Low risk", "The customer is frustrated.", [], no_flags)
    )
    flagged = build_allowed_facts(_account("C"))
    assert validate_explanation("Medium risk", "The customer is frustrated.", [], flagged) == []

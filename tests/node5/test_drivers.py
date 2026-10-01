"""Node 5 presentation of per-account model drivers (§2.12b/§4.4b, 2026-10-01).

Node 5 copies Node 4 ``driver_details`` verbatim, words them against the model
reference profile (hazard language, never "portfolio average"/"relative risk"),
falls back to the legacy list when absent, and registers driver facts so an
opt-in LLM explanation citing them is not falsely rejected.
"""

from __future__ import annotations

import json

import pytest

from config.loader import load_node4_config
from config.models import Node5Config
from node4.node import run_node4
from node5.llm.explainer import build_prompt
from node5.node import run_node5
from node5.rendering.html import render_html
from node5.report.driver_text import detail_phrase, evidence_description, summary_sentence
from node5.report.explanation_validator import build_allowed_facts, validate_explanation
from schemas.node2 import DriverDetail
from schemas.node4 import Node4Output, RankedAccount
from tests.node4.conftest import make_node3, make_signal
from tests.node4.test_drivers import _node2

# The orchestrator's no-support baseline: every customer no_data, zero threads.
BASELINE = make_node3(
    [
        make_signal(cid, support_data_status="no_data", overall_signal_confidence=0.0,
                    n_threads_in_window=0)
        for cid in "ABCD"
    ]
)


def _node4(with_contributions: bool = True) -> Node4Output:
    return run_node4(_node2(with_contributions), BASELINE, load_node4_config("4"))

USAGE = DriverDetail(
    feature="usage_frequency",
    kind="numeric",
    value=3.0,
    reference=12.4,
    contribution=0.42,
    hazard_ratio=1.105,
    reliable=True,
)
PLAN = DriverDetail(
    feature="plan_tier",
    kind="categorical",
    value="starter",
    reference="enterprise",
    contribution=0.35,
    hazard_ratio=1.105,
    reliable=True,
)


@pytest.fixture(scope="module")
def node4_v4() -> Node4Output:
    return _node4()


def _account(output: Node4Output, customer_id: str) -> RankedAccount:
    return next(a for a in output.ranked_accounts if a.customer_id == customer_id)


def test_wording_is_hazard_language_against_reference_profile() -> None:
    assert evidence_description([USAGE, PLAN]) == (
        "Model drivers relative to the model reference profile: "
        "usage_frequency (below the reference profile); plan_tier = starter."
    )
    assert summary_sentence(USAGE, 0.77) == (
        "The model rates this account's churn hazard above the model reference profile "
        "mainly because usage_frequency (3) is below the reference profile value (12.4)."
    )
    assert summary_sentence(PLAN, 0.77).endswith(
        "the account is on plan_tier = starter (reference category: enterprise)."
    )
    assert detail_phrase(USAGE) == "usage_frequency = 3 (below the model reference profile, 12.4)"
    texts = [
        evidence_description([USAGE, PLAN]),
        summary_sentence(USAGE, 0.77),
        detail_phrase(USAGE),
        detail_phrase(PLAN),
    ]
    for text in texts:
        assert "portfolio average" not in text
        assert "relative risk" not in text
        assert "causes" not in text


def test_no_overall_above_claim_when_total_is_not_positive() -> None:
    for total in (-0.4, 0.0, None):
        sentence = summary_sentence(USAGE, total)
        assert "above the model reference profile" not in sentence
        assert "usage_frequency (3) is below the reference profile value (12.4)" in sentence


def test_report_copies_driver_details_and_writes_per_account_text(
    node4_v4: Node4Output, node5_config: Node5Config, action_rules: object
) -> None:
    output = run_node5(node4_v4, node5_config, action_rules=action_rules)  # type: ignore[arg-type]
    report = {a.customer_id: a for a in output.report.priority_accounts}
    a = report["A"]
    source = _account(node4_v4, "A")
    assert a.quantitative_summary.driver_details == source.quantitative.driver_details
    assert a.quantitative_summary.top_drivers == ["usage_frequency", "plan_tier"]
    assert "usage_frequency (3) is below the reference profile value (12.4)" in a.summary
    node2_evidence = [e for e in a.evidence if e.source == "node2"]
    assert node2_evidence[0].description.startswith("Model drivers relative to")
    assert node2_evidence[0].node2_reference is not None
    assert node2_evidence[0].node2_reference.feature_ref == "usage_frequency"
    # C has no positive driver: no driver sentence.
    assert "reference profile" not in report["C"].summary


def test_legacy_text_without_driver_details(
    node5_config: Node5Config, action_rules: object
) -> None:
    node4 = _node4(with_contributions=False)
    output = run_node5(node4, node5_config, action_rules=action_rules)  # type: ignore[arg-type]
    account = output.report.priority_accounts[0]
    assert account.quantitative_summary.driver_details == []
    node2_evidence = [e for e in account.evidence if e.source == "node2"]
    assert node2_evidence[0].description == (
        "Primary model drivers: plan_tier_starter, support_tickets_90d."
    )
    assert "Primary model drivers: plan_tier_starter, support_tickets_90d" in render_html(output)


def test_html_renders_directional_drivers_escaped(
    node4_v4: Node4Output, node5_config: Node5Config, action_rules: object
) -> None:
    output = run_node5(node4_v4, node5_config, action_rules=action_rules)  # type: ignore[arg-type]
    html = render_html(output)
    assert "Model drivers raising this account's churn hazard:" in html
    assert "usage_frequency = 3 (below the model reference profile, 12.4)" in html
    assert "plan_tier = starter (reference category: enterprise)" in html


def test_prompt_includes_driver_details_only_when_present(node4_v4: Node4Output) -> None:
    prompt = build_prompt(_account(node4_v4, "A"), "A")
    payload = json.loads(prompt.split("STRUCTURED INPUT:\n", 1)[1])
    assert payload["quantitative"]["driver_details"][0]["feature"] == "usage_frequency"
    legacy = _node4(with_contributions=False)
    assert "driver_details" not in build_prompt(legacy.ranked_accounts[0], "A")


def test_validator_accepts_driver_facts_and_hazard_language(node4_v4: Node4Output) -> None:
    account = _account(node4_v4, "A")
    allowed = build_allowed_facts(account)
    level = account.combined_risk_level.value
    summary = (
        f"This account is {level} risk. Its usage_frequency of 3.0 is below the model "
        "reference profile value of 12.4, which the model associates with a higher "
        "churn hazard (hazard ratio 1.105, log-hazard contribution 0.42)."
    )
    assert validate_explanation("Model drivers", summary, [], allowed) == []


def test_validator_still_rejects_invented_driver_numbers(node4_v4: Node4Output) -> None:
    allowed = build_allowed_facts(_account(node4_v4, "A"))
    violations = validate_explanation(
        "Model drivers", "Its usage_frequency of 7.7 is below the reference of 99.", [], allowed
    )
    assert any("7.7" in v for v in violations)
    assert any("99" in v for v in violations)

"""Milestone A — rendering is separate from generation (architecture §5.30)."""

from __future__ import annotations

from node5.node import run_node5
from node5.rendering.html import render_html
from node5.rendering.json import render_json
from tests.node5.conftest import make_sample_inputs


def _output(config, action_rules):
    node4, node3 = make_sample_inputs()
    return run_node5(node4, config, node3_output=node3, action_rules=action_rules)


def test_json_is_deterministic(node5_config, action_rules) -> None:
    output = _output(node5_config, action_rules)
    assert render_json(output) == render_json(output)


def test_html_contains_decision_fields(node5_config, action_rules) -> None:
    output = _output(node5_config, action_rules)
    html = render_html(output)
    assert "Customer Risk Report" in html
    for report in output.report.priority_accounts:
        assert report.display_name in html
        assert report.risk_level.value.upper() in html


def test_html_contains_quantitative_and_support_sections(node5_config, action_rules) -> None:
    output = _output(node5_config, action_rules)
    html = render_html(output)
    assert "Quantitative signals" in html
    assert "Support signals" in html
    # Account-level values are presented from the validated object only.
    account = next(a for a in output.report.priority_accounts if a.customer_id == "B")
    assert account.support_summary.support_data_status.value in html
    assert account.risk_level.value.upper() in html


def test_html_escapes_text(node5_config, action_rules) -> None:
    output = _output(node5_config, action_rules)
    html = render_html(output)
    assert "<script>" not in html

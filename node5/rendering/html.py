"""Dependency-free HTML renderer (architecture §5.30/§5.31, F-7).

Consumes the same validated `Node5Output` as the JSON renderer. No separate
business logic: it only formats facts already present in the report.
"""

from __future__ import annotations

from html import escape

from node5.report.driver_text import detail_phrase
from schemas.node5 import ChurnedSection, CustomerReport, Node5Output

#: Churned customer ids listed in the static report; the full list is in the JSON.
MAX_CHURNED_IDS = 200


def _quantitative_signals(account: CustomerReport) -> str:
    quantitative = account.quantitative_summary
    rows = [f"<li>Customer state: {escape(quantitative.customer_state.value)}</li>"]
    if quantitative.churn_prob_90d_forward is not None:
        rows.append(
            "<li>Forward 90-day churn probability: "
            f"{quantitative.churn_prob_90d_forward:.3f}</li>"
        )
    if quantitative.lift_vs_base is not None:
        rows.append(f"<li>Lift vs portfolio average: {quantitative.lift_vs_base:.2f}x</li>")
    if quantitative.forward_status == "beyond_follow_up":
        rows.append("<li>Forward estimate: beyond the model's observed follow-up</li>")
    if quantitative.risk_score is not None:
        rows.append(f"<li>Risk score: {quantitative.risk_score:.3f}</li>")
    if quantitative.survival_prob_90d is not None:
        rows.append(f"<li>90-day survival probability: {quantitative.survival_prob_90d:.3f}</li>")
    if quantitative.driver_details:
        drivers = "; ".join(escape(detail_phrase(d)) for d in quantitative.driver_details)
        rows.append(f"<li>Model drivers raising this account's churn hazard: {drivers}</li>")
    elif quantitative.top_drivers:
        drivers = ", ".join(escape(driver) for driver in quantitative.top_drivers)
        rows.append(f"<li>Primary model drivers: {drivers}</li>")
    return f"<h4>Quantitative signals</h4><ul>{''.join(rows)}</ul>"


def _confidence_factors(account: CustomerReport) -> str:
    factors = account.confidence_factors
    if factors is None:
        return ""
    rows = [
        f"<li>Model quality: {factors.model:.2f}</li>",
        f"<li>Estimate precision: {factors.precision:.2f}</li>",
        f"<li>Customer history: {factors.history:.2f}</li>",
        f"<li>Quantitative confidence: {factors.quantitative:.2f}</li>",
    ]
    if factors.support is not None:
        rows.append(f"<li>Support evidence: {factors.support:.2f}</li>")
    return f"<h4>Confidence breakdown</h4><ul>{''.join(rows)}</ul>"


def _churned_section(churned: ChurnedSection) -> str:
    if not churned.n_churned:
        return ""
    shown = churned.customer_ids[:MAX_CHURNED_IDS]
    items = "".join(f"<li>{escape(customer_id)}</li>" for customer_id in shown)
    more = churned.n_churned - len(shown)
    tail = f"<p>…and {more} more (see the JSON report).</p>" if more > 0 else ""
    return (
        '<section id="churned">\n<h2>Already Churned</h2>\n'
        f"<details><summary>{churned.n_churned} customers had already churned "
        "and are not ranked.</summary>"
        f"<ul>{items}</ul>{tail}</details>\n</section>\n"
    )


def _support_signals(account: CustomerReport) -> str:
    support = account.support_summary
    rows = [
        f"<li>Support data: {escape(support.support_data_status.value)}</li>",
        f"<li>Signal strength: {escape(support.signal_strength.value)}</li>",
        f"<li>Churn language detected: {'yes' if support.churn_language_detected else 'no'}</li>",
        f"<li>Escalation signal: {'yes' if support.escalation_signal else 'no'}</li>",
    ]
    if support.top_flags:
        flags = ", ".join(
            f"{escape(flag.flag_type.value)} ({escape(flag.severity.value)}, "
            f"{escape(flag.signal_strength.value)})"
            for flag in support.top_flags
        )
        rows.append(f"<li>Top flags: {flags}</li>")
    return f"<h4>Support signals</h4><ul>{''.join(rows)}</ul>"


def _account_block(account: CustomerReport) -> str:
    reasons = "".join(
        f"<li>{escape(reason.statement)} "
        f"<em>({escape(reason.source)}, {escape(reason.severity.value)})</em></li>"
        for reason in account.primary_reasons
    )
    evidence = "".join(
        f"<li>{escape(item.description)}"
        + (
            f" — “{escape(item.node3_reference.evidence_text)}”"
            if item.node3_reference is not None
            else ""
        )
        + "</li>"
        for item in account.evidence
    )
    notes = "".join(f"<li>{escape(note)}</li>" for note in account.data_quality_notes)
    recommendation = (
        f"<p><strong>Recommended action:</strong> {escape(account.recommended_action)}</p>"
        if account.recommended_action
        else ""
    )
    rank_label = f"#{account.rank}" if account.rank is not None else "unranked"
    provenance = "LLM-drafted" if account.explanation_source == "llm" else "template"
    return (
        f'<section class="account">'
        f"<h3>{rank_label} {escape(account.display_name)}</h3>"
        f"<p><strong>Risk:</strong> {escape(account.risk_level.value.upper())} "
        f"&nbsp; <strong>Confidence:</strong> {account.combined_confidence:.2f} "
        f'&nbsp; <span class="provenance">explanation: {provenance}</span></p>'
        f"<p>{escape(account.summary)}</p>"
        f"<h4>Why this account matters</h4><ul>{reasons}</ul>"
        f"{_quantitative_signals(account)}"
        f"{_confidence_factors(account)}"
        f"{_support_signals(account)}"
        f"<h4>Evidence</h4><ul>{evidence}</ul>"
        f"<h4>Data quality</h4><ul>{notes}</ul>"
        f"{recommendation}"
        f"</section>"
    )


def render_html(output: Node5Output) -> str:
    """Render the validated report to a standalone HTML document."""
    report = output.report
    distribution = report.risk_distribution
    priority = "".join(_account_block(account) for account in report.priority_accounts)
    insufficient = "".join(
        _account_block(account) for account in report.insufficient_data_accounts
    )
    methodology = "".join(
        f"<p>{escape(paragraph)}</p>" for paragraph in report.methodology.split("\n\n")
    )
    notes = "".join(f"<li>{escape(note)}</li>" for note in report.data_quality.notes)
    churned = _churned_section(report.churned)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>{escape(report.title)}</title>
</head>
<body>
<header>
<h1>{escape(report.title)}</h1>
<p>Reference date: {report.reference_date.isoformat()}</p>
</header>
<section id="executive-summary">
<h2>Executive Summary</h2>
<pre>{escape(report.executive_summary)}</pre>
</section>
<section id="risk-distribution">
<h2>Risk Distribution</h2>
<table>
<tr><th>Risk Level</th><th>Customers</th></tr>
<tr><td>Critical</td><td>{distribution.critical}</td></tr>
<tr><td>High</td><td>{distribution.high}</td></tr>
<tr><td>Medium</td><td>{distribution.medium}</td></tr>
<tr><td>Low</td><td>{distribution.low}</td></tr>
<tr><td>Insufficient data</td><td>{distribution.insufficient_data}</td></tr>
</table>
</section>
<section id="priority-accounts">
<h2>Priority Accounts</h2>
{priority}
</section>
<section id="insufficient-data">
<h2>Insufficient Data</h2>
<p>Insufficient data is not equivalent to low risk.</p>
{insufficient}
</section>
{churned}<section id="data-quality">
<h2>Data Quality</h2>
<ul>{notes}</ul>
</section>
<section id="methodology">
<h2>Methodology</h2>
{methodology}
</section>
<footer>
<p>Report version {escape(output.metadata.report_version)} &middot;
Generated at {output.metadata.generated_at.isoformat()} &middot;
Reference date {output.metadata.reference_date.isoformat()}</p>
</footer>
</body>
</html>
"""

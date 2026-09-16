"""Milestone A — deterministic sections (architecture §5.6/§5.7/§5.23/§5.24)."""

from __future__ import annotations

from node5.report.deterministic_sections import (
    build_data_quality_notes,
    build_executive_summary,
    build_methodology,
    build_risk_distribution,
)
from tests.node5.conftest import make_sample_node4


def test_risk_distribution_matches_summary_stats(node5_config) -> None:
    output = make_sample_node4()
    distribution = build_risk_distribution(output)
    stats = output.summary_stats
    assert distribution.critical == stats.n_critical
    assert distribution.high == stats.n_high
    assert distribution.medium == stats.n_medium
    assert distribution.low == stats.n_low
    assert distribution.insufficient_data == stats.n_insufficient_data


def test_executive_summary_uses_node4_numbers(node5_config) -> None:
    output = make_sample_node4()
    summary = build_executive_summary(output, node5_config)
    stats = output.summary_stats
    assert f"{stats.n_critical} accounts were classified as Critical," in summary
    assert f"{stats.n_insufficient_data} customers had insufficient data" in summary


def test_data_quality_notes_are_derived(node5_config) -> None:
    output = make_sample_node4()
    notes = build_data_quality_notes(output, node5_config)
    assert f"Reference date: {output.reference_date.isoformat()}." in notes
    assert any("insufficient data" in note for note in notes)


def test_methodology_is_non_empty_and_deterministic() -> None:
    assert build_methodology() == build_methodology()
    assert "Node 4" in build_methodology()

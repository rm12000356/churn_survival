"""Reproducibility test (architecture §1.3, ROADMAP Task 2.11).

Tenure for active customers is always computed against the declared
`reference_date`, never "today" at runtime. Running identical raw input with
different wall-clock timestamps must yield identical derived output; with an
identical injected ingest timestamp the whole Node 1 output is bit-identical.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

from config.models import Node1Config
from node1.node import run_node1

FIXTURES = Path(__file__).parent.parent / "adapters" / "fixtures"
REFERENCE_DATE = date(2026, 8, 15)
RAW = FIXTURES / "clean_customers.csv"


def _project(output) -> dict:
    data = output.model_dump(mode="json")
    for record in data["canonical_dataset"]:
        record["meta"]["ingested_at"] = "IGNORED_PROVENANCE"
    return data


def test_different_wall_clock_yields_identical_derived_output(
    fresh_settings, node1_config: Node1Config
) -> None:
    first = run_node1(
        RAW,
        reference_date=REFERENCE_DATE,
        config=node1_config,
        now=datetime(2026, 8, 15, 12, 0, 0, tzinfo=UTC),
    )
    later = run_node1(
        RAW,
        reference_date=REFERENCE_DATE,
        config=node1_config,
        now=datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC),  # "today" has moved
    )
    assert _project(first) == _project(later)
    assert first.canonical_dataset[0].tenure == later.canonical_dataset[0].tenure


def test_same_declared_reference_date_yields_bit_identical_output(
    fresh_settings, node1_config: Node1Config
) -> None:
    stamp = datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC)
    first = run_node1(RAW, reference_date=REFERENCE_DATE, config=node1_config, now=stamp)
    second = run_node1(RAW, reference_date=REFERENCE_DATE, config=node1_config, now=stamp)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_reference_date_drives_active_tenure(fresh_settings, node1_config: Node1Config) -> None:
    stamp = datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC)
    ref = date(2026, 8, 15)
    other_ref = date(2026, 9, 15)
    output_ref = run_node1(RAW, reference_date=ref, config=node1_config, now=stamp)
    output_other = run_node1(RAW, reference_date=other_ref, config=node1_config, now=stamp)
    active_id = "cus_1001"
    first = next(r for r in output_ref.canonical_dataset if r.customer_id == active_id)
    second = next(r for r in output_other.canonical_dataset if r.customer_id == active_id)
    assert first.tenure == 521.0
    assert second.tenure == first.tenure + 31  # reference_date moved forward by a month

"""Tenure calculation and censoring helpers (architecture §1.3, ROADMAP Task 2.6).

The observation window is the heart of the canonical record:
- Churned customers (`event_observed == 1`): ``observation_end`` = actual churn date.
- Active customers (`event_observed == 0`): ``observation_end`` = dataset ``reference_date``.

The declared ``reference_date`` is a *cut-off*, never "today" at runtime, so
rerunning identical raw data on a later calendar day yields identical tenure.
"""

from __future__ import annotations

from datetime import date

from adapters.util import parse_date


def observation_end_for(
    event_observed: int | None, raw_end: object, reference_date: date
) -> date | None:
    """Determine the canonical ``observation_end`` for a record (censoring rule).

    Churned records must have an actual churn date. Active (or undetermined)
    records are censored at the declared ``reference_date``.
    """
    if event_observed == 1:
        return parse_date(raw_end)
    return reference_date


def compute_tenure(observation_start: date, observation_end: date) -> float:
    """Tenure in days, always a finite non-negative float (§1.3)."""
    return float((observation_end - observation_start).days)

from __future__ import annotations

from datetime import date

from adapters.util import parse_date


def observation_end_for(
    event_observed: int | None, raw_end: object, reference_date: date
) -> date | None:
    if event_observed == 1:
        return parse_date(raw_end)
    return reference_date


def compute_tenure(observation_start: date, observation_end: date) -> float:
    return float((observation_end - observation_start).days)

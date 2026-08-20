"""Synthetic canonical-record factory + shared config fixtures for Node 2 tests.

The generated datasets are deterministic (seeded) and exercise the full
fit/score path: 3 approved predictors (one categorical, two numeric), a spread
of tenures, and a configurable event rate.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

import numpy as np
import pytest

from config.loader import load_node2_config
from config.models import Node2Config
from schemas.canonical import CanonicalRecord

PREDICTORS = ["plan_tier", "contract_length_months", "usage_frequency"]
REFERENCE_DATE = date(2026, 8, 15)
REFERENCE_ORDINAL = REFERENCE_DATE.toordinal()


def make_record(
    i: int,
    *,
    tenure: int,
    event: int,
    event_time: int | None = None,
    plan_tier: str | None = "basic",
    contract_length_months: float | None = 12.0,
    usage_frequency: float | None = 25.0,
) -> CanonicalRecord:
    """A single canonical record ending inside the reference-date window.

    ``tenure`` is the observation-window length; event records end at
    ``event_time`` days into the window (censored records end exactly on the
    reference date). The canonical tenure is derived from the dates so it always
    matches ``(observation_end - observation_start).days``.
    """
    obs_start = date.fromordinal(REFERENCE_ORDINAL - tenure)
    observed = min(tenure, event_time) if event_time is not None else tenure
    obs_end = date.fromordinal(obs_start.toordinal() + observed)
    return CanonicalRecord(
        customer_id=f"cus_{i:04d}",
        observation_start=obs_start,
        observation_end=obs_end,
        event_observed=event,
        tenure=float(observed),
        core_features={
            "plan_tier": plan_tier,
            "contract_length_months": contract_length_months,
            "usage_frequency": usage_frequency,
        },
        meta={
            "source_adapter": "test_adapter",
            "mapping_version": "map_test_v1",
            "ingested_at": "2026-08-17T00:00:00Z",
            "reference_date": REFERENCE_DATE,
        },
    )


def synthetic_dataset(
    n: int = 400,
    *,
    seed: int = 42,
    event_rate: float = 0.35,
    tenure_scale: float = 400.0,
    missing_fraction: float = 0.0,
    plans: Sequence[str] = ("basic", "pro", "enterprise"),
) -> list[CanonicalRecord]:
    """Deterministic synthetic customers spanning ~0-1.5 years of tenure."""
    rng = np.random.default_rng(seed)
    records: list[CanonicalRecord] = []
    for i in range(n):
        tenure = max(1, int(rng.exponential(tenure_scale)))
        event = int(rng.random() < event_rate)
        event_time = min(tenure, max(1, int(rng.exponential(tenure_scale)))) if event else None
        missing = rng.random() < missing_fraction
        plan_tier = str(rng.choice(list(plans))) if not missing else None
        usage = float(rng.normal(25.0, 8.0)) if not missing else None
        records.append(
            make_record(
                i,
                tenure=tenure,
                event=event,
                event_time=event_time,
                plan_tier=plan_tier,
                contract_length_months=float(rng.choice([6, 12, 24, 36])),
                usage_frequency=usage,
            )
        )
    return records


def make_config(**overrides: Any) -> Node2Config:
    """A Node2Config with the v1 defaults, selectively overridden."""
    base = load_node2_config("1").model_dump()
    base.update(overrides)
    return Node2Config.model_validate(base)


@pytest.fixture(scope="session")
def node2_config() -> Node2Config:
    return load_node2_config("1")

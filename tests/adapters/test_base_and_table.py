from __future__ import annotations

import pandas as pd
import pytest

from adapters._table import coerce_string, obvious_row_maps
from adapters.base import BaseAdapter
from adapters.clean_csv import CleanCsvAdapter
from router.fingerprint import extract_fingerprint


class _BareAdapter(BaseAdapter):
    name = "bare"
    version = "1.0.0"

    def transform(self, raw_data, reference_date: str) -> list[dict]:
        return []


def test_matches_signature_default_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        _BareAdapter().matches_signature(object())


def test_can_handle_default_delegates_to_signature(fresh_settings) -> None:
    frame = pd.read_csv("tests/adapters/fixtures/clean_customers.csv")
    assert CleanCsvAdapter().can_handle(frame) is True


def test_split_features_splits_approved_and_extra() -> None:
    core, extra = BaseAdapter.split_features(
        {"plan_tier": "Enterprise", "foo": 1, "bar": "x"},
        ["plan_tier"],
    )
    assert core == {"plan_tier": "Enterprise"}
    assert extra == {"foo": 1, "bar": "x"}


def test_split_features_always_represents_approved_keys() -> None:
    core, extra = BaseAdapter.split_features({"other": 1}, ["plan_tier"])
    assert core == {"plan_tier": None}
    assert "other" in extra


def test_coerce_string_blank_to_none() -> None:
    assert coerce_string(None) is None
    assert coerce_string("  ") is None
    assert coerce_string(" abc ") == "abc"


def test_obvious_row_maps_numeric_event_fallback() -> None:
    frame = pd.DataFrame(
        {
            "customer_id": ["a", "b"],
            "observation_start": ["2025-01-01", "2025-01-01"],
            "observation_end": ["2026-08-15", "2026-07-01"],
            "event_observed": ["1", "0"],
            "plan_tier": ["P", "Q"],
        }
    )
    row_maps = obvious_row_maps(frame)
    assert [row["event_observed"] for row in row_maps] == [1, 0]


def test_obvious_row_maps_keeps_unmapped_columns_as_extra() -> None:
    frame = pd.DataFrame(
        {
            "customer_id": ["a"],
            "observation_start": ["2025-01-01"],
            "observation_end": ["2026-08-15"],
            "event_observed": ["Active"],
            "plan_tier": ["P"],
            "sneaky_column": ["keep-me"],
        }
    )
    row_maps = obvious_row_maps(frame)
    assert row_maps[0]["extra_features"]["sneaky_column"] == "keep-me"


def test_fingerprint_of_clean_fixture_is_single_table() -> None:
    frame = pd.read_csv("tests/adapters/fixtures/clean_customers.csv")
    fingerprint = extract_fingerprint(frame)
    assert fingerprint.sheet_names == []

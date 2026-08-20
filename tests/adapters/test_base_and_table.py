from __future__ import annotations

import io

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


def test_coerce_string_nan_to_none() -> None:
    assert coerce_string(float("nan")) is None
    assert coerce_string(pd.NA) is None
    assert coerce_string("nan") == "nan"  # a literal source string is preserved


def test_obvious_row_maps_blank_cells_are_missing() -> None:
    frame = pd.read_csv(
        io.StringIO(
            "customer_id,observation_start,observation_end,event_observed,"
            "plan_tier,contract_length_months,usage_frequency\n"
            "a,2025-01-01,2026-08-15,0,pro,12,28.4\n"
            ",2025-01-01,2026-07-01,1,,,4.1\n"
            "c,2025-01-01,2026-07-01,1,pro,12,\n"
        )
    )
    rows = obvious_row_maps(frame)

    assert rows[1]["customer_id"] is None  # blank customer_id -> None, never "nan"
    assert rows[1]["core_features"]["plan_tier"] is None  # blank string core
    assert rows[1]["core_features"]["contract_length_months"] is None  # blank numeric
    assert rows[1]["core_features"]["usage_frequency"] == 4.1

    assert rows[2]["core_features"]["usage_frequency"] is None  # blank numeric
    assert rows[0]["core_features"]["contract_length_months"] == 12.0  # non-blank preserved

    for row in rows:
        assert "nan" not in str(row["customer_id"])
        assert "nan" not in str(row["core_features"])


def test_clean_csv_blank_string_cells_become_missing() -> None:
    frame = pd.read_csv(
        io.StringIO(
            "customer_id,observation_start,observation_end,event_observed,"
            "plan_tier,contract_length_months\n"
            "a,2025-01-01,2026-08-15,0,pro,12\n"
            ",2025-01-01,2026-07-01,1,,6\n"
        )
    )
    records = CleanCsvAdapter().transform(frame, "2026-08-15")
    assert records[1]["customer_id"] is None
    assert records[1]["core_features"]["plan_tier"] is None
    assert records[1]["core_features"]["contract_length_months"] == 6.0
    assert "nan" not in str(records)


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

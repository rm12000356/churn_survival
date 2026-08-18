"""Deterministic adapter for clean single-sheet CSVs (architecture §1.5, ROADMAP Task 2.4).

Matches a single table whose columns are already the canonical names
(``customer_id``, ``observation_start``, ``observation_end``,
``event_observed``) plus core/extra feature columns. Column names must be
exact; anything else belongs to a more specific adapter.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from adapters._table import obvious_row_maps, rows_to_records
from adapters.base import BaseAdapter

REQUIRED_COLUMNS = {"customer_id", "observation_start", "observation_end", "event_observed"}


class CleanCsvAdapter(BaseAdapter):
    """Parses a single obvious-column table into canonical records."""

    name = "clean_csv"
    version = "1.0.0"
    confidence = 0.95
    priority = 10

    def matches_signature(self, fingerprint: Any) -> bool:
        return not fingerprint.sheet_names and REQUIRED_COLUMNS.issubset(
            set(fingerprint.column_names)
        )

    def transform(self, raw_data: Any, reference_date: str) -> list[dict]:
        if isinstance(raw_data, dict):
            raise TypeError(f"{self.name} handles a single table; got {len(raw_data)} sheets")
        if not isinstance(raw_data, pd.DataFrame):
            raise TypeError(f"{self.name} expects a DataFrame, got {type(raw_data).__name__}")
        return rows_to_records(self, obvious_row_maps(raw_data), reference_date)

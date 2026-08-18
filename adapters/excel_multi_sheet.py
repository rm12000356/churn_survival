"""Deterministic adapter for multi-sheet Excel workbooks (architecture §1.5, ROADMAP Task 2.4).

Matches a workbook with a recognizable customer sheet (``Customers``,
``customer``, ``Accounts``, ...). The customer sheet is expected to carry the
obvious canonical column names; the remaining sheets are treated as context and
ignored by this adapter.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from adapters._table import obvious_row_maps, rows_to_records
from adapters.base import BaseAdapter

CUSTOMER_SHEET_NAMES = {"customers", "customer", "accounts", "account"}


class ExcelMultiSheetAdapter(BaseAdapter):
    """Parses the customer sheet of a known multi-sheet workbook."""

    name = "excel_multi_sheet"
    version = "1.0.0"
    confidence = 0.90
    priority = 20

    def matches_signature(self, fingerprint: Any) -> bool:
        if not fingerprint.sheet_names or len(fingerprint.sheet_names) < 2:
            return False
        return bool(CUSTOMER_SHEET_NAMES.intersection(s.lower() for s in fingerprint.sheet_names))

    def _customer_sheet(self, raw_data: dict[str, pd.DataFrame]) -> str:
        for sheet in raw_data:
            if sheet.lower() in CUSTOMER_SHEET_NAMES:
                return sheet
        return next(iter(raw_data))

    def transform(self, raw_data: Any, reference_date: str) -> list[dict]:
        if not isinstance(raw_data, dict):
            raise TypeError(
                f"{self.name} handles a multi-sheet workbook, got {type(raw_data).__name__}"
            )
        if not raw_data:
            raise ValueError(f"{self.name} received an empty workbook")
        sheet = self._customer_sheet(raw_data)
        frame = raw_data[sheet]
        if not isinstance(frame, pd.DataFrame):
            raise TypeError(f"sheet {sheet!r} is not a DataFrame")
        return rows_to_records(self, obvious_row_maps(frame), reference_date)

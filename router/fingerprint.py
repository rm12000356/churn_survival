"""Fingerprint extraction for the router (architecture §0.1, §1.6, ROADMAP Task 2.2).

A fingerprint captures the *schema signature* of incoming data — column names,
sheet names, dtypes, row count — not sample statistics alone. Same file in,
same fingerprint out; ``headers_hash`` is the sha256 of the sorted header names.
"""

from __future__ import annotations

import hashlib
from typing import Any

from schemas.mapping import SourceFingerprint


def header_hash(headers: list[str]) -> str:
    """sha256 hex of the sorted header names (architecture §1.6)."""
    digest = hashlib.sha256()
    for header in sorted(headers):
        digest.update(str(header).encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def _columns_and_dtypes(frame: Any) -> tuple[list[str], dict[str, str]]:
    columns = [str(col) for col in frame.columns]
    dtypes = {str(col): str(frame[col].dtype) for col in frame.columns}
    return columns, dtypes


def primary_sheet_name(raw: dict[Any, Any]) -> str:
    """The workbook sheet that holds the data: the one with the most rows.

    A workbook often opens with a data dictionary or notes sheet, so the first
    sheet is not reliably the data. Ties go to the earlier sheet in workbook
    order, so the choice is deterministic.
    """
    if not raw:
        raise ValueError("multi-sheet input has no sheets")
    best_name, best_rows = "", -1
    for name, frame in raw.items():
        rows = len(frame)
        if rows > best_rows:
            best_name, best_rows = str(name), rows
    return best_name


def extract_fingerprint(raw: Any, *, n_sample_rows: int = 25) -> SourceFingerprint:
    """Extract a ``SourceFingerprint`` from a DataFrame or a dict of DataFrames.

    A single table (CSV, single-sheet) has ``sheet_names == []``; a multi-sheet
    workbook is a ``dict[str, DataFrame]`` and records its sheet names plus the
    ``primary_sheet`` (most rows) whose columns the fingerprint describes.
    """
    import pandas as pd

    primary_name: str | None = None
    if isinstance(raw, dict):
        sheet_names = [str(sheet) for sheet in raw]
        if not sheet_names:
            raise ValueError("multi-sheet input has no sheets")
        primary_name = primary_sheet_name(raw)
        primary = {str(name): frame for name, frame in raw.items()}[primary_name]
        columns, dtypes = _columns_and_dtypes(primary)
        sample_rows = min(len(primary), n_sample_rows)
    elif isinstance(raw, pd.DataFrame):
        sheet_names: list[str] = []
        columns, dtypes = _columns_and_dtypes(raw)
        sample_rows = min(len(raw), n_sample_rows)
    else:
        raise TypeError(
            f"cannot fingerprint raw data of type {type(raw).__name__}; "
            "expected pandas.DataFrame or dict[str, pandas.DataFrame]"
        )

    return SourceFingerprint(
        headers_hash=header_hash(columns),
        sheet_names=sheet_names,
        primary_sheet=primary_name,
        column_names=columns,
        sample_dtypes=dtypes,
        n_sample_rows=sample_rows,
    )

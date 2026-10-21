from __future__ import annotations

from pathlib import Path

import pandas as pd

__all__ = ["SUPPORTED_TABLE_EXTENSIONS", "read_raw_table"]

SUPPORTED_TABLE_EXTENSIONS = frozenset({".csv", ".xlsx", ".xls"})


def read_raw_table(
    path: str | Path,
    *,
    supported_extensions: frozenset[str] = SUPPORTED_TABLE_EXTENSIONS,
) -> pd.DataFrame | dict[str, pd.DataFrame]:
    """Read a raw customer table from CSV or Excel.

    CSV is read with ``low_memory=False`` so each column is typed in a single
    pass: with the default chunked reader a column that is numeric except for a
    late non-numeric value can come back as a mix of ints and strings.
    ``encoding="utf-8"`` is explicit so behaviour does not depend on the
    platform default. Excel workbooks are returned as a sheet-name -> frame
    mapping.

    Known limitation: pandas still coerces an all-numeric identifier column, so
    a leading-zero id like ``00123`` becomes ``123``. Preserve such ids at the
    source (or in the mapping layer) — see the deferred option to peek headers
    and read id columns as ``str``.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"raw data file not found: {path}")
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path, low_memory=False, encoding="utf-8")
    if suffix in {".xlsx", ".xls"}:
        frames = pd.read_excel(path, sheet_name=None)
        return {str(name): frame for name, frame in frames.items()}
    raise ValueError(
        f"unsupported raw-data extension {suffix!r}; "
        f"supported: {sorted(supported_extensions)}"
    )

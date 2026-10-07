from __future__ import annotations

from pathlib import Path

import pandas as pd

from churn_io.tables import read_raw_table
from router.fingerprint import extract_fingerprint


def _mixed_late_value_csv(path: Path, n: int = 300_000) -> None:
    """A numeric column with one late non-numeric value, long enough to span
    pandas' default chunked CSV reader."""
    lines = ["id,val"]
    lines.extend(f"{i},{i}" for i in range(n // 2))
    lines.append("x,abc")
    lines.extend(f"{i},{i}" for i in range(n // 2 + 1, n))
    path.write_text("\n".join(lines), encoding="utf-8")


def test_read_raw_table_types_the_whole_column_in_one_pass(tmp_path: Path) -> None:
    path = tmp_path / "mixed.csv"
    _mixed_late_value_csv(path)

    frame = read_raw_table(path)

    assert isinstance(frame, pd.DataFrame)
    # low_memory=False reads the column as a whole, so it is consistently str.
    assert {type(value).__name__ for value in frame["val"].tolist()} == {"str"}


def test_default_reader_is_inconsistent_on_the_same_file(tmp_path: Path) -> None:
    """Documents why low_memory=False matters (tied to the pandas <3 pin)."""
    path = tmp_path / "mixed.csv"
    _mixed_late_value_csv(path)

    default = pd.read_csv(path)

    assert {type(value).__name__ for value in default["val"].tolist()} != {"str"}


def test_read_raw_table_is_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "ok.csv"
    path.write_text(
        "customer_id,status\nc1,active\nc2,churned\n", encoding="utf-8"
    )

    first = read_raw_table(path)
    second = read_raw_table(path)

    assert isinstance(first, pd.DataFrame)
    assert isinstance(second, pd.DataFrame)
    pd.testing.assert_frame_equal(first, second)
    assert extract_fingerprint(first).sample_dtypes == extract_fingerprint(second).sample_dtypes


def test_read_raw_table_rejects_unknown_extension(tmp_path: Path) -> None:
    path = tmp_path / "notes.json"
    path.write_text("[]", encoding="utf-8")
    try:
        read_raw_table(path)
    except ValueError as exc:
        assert "unsupported raw-data extension" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("expected ValueError")

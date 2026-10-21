from __future__ import annotations

from pathlib import Path

import pytest

from churn_io.atomic import atomic_write


def _leftover(path: Path) -> list[str]:
    return sorted(p.name for p in path.iterdir() if p.name != "target.json")


def test_atomic_write_replaces_target(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("old", encoding="utf-8")

    atomic_write(target, "new")

    assert target.read_text(encoding="utf-8") == "new"
    assert _leftover(tmp_path) == []


def test_atomic_write_failure_leaves_original_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "target.json"
    target.write_text("old", encoding="utf-8")

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("churn_io.atomic.os.replace", boom)
    with pytest.raises(OSError):
        atomic_write(target, "new")

    assert target.read_text(encoding="utf-8") == "old"
    assert _leftover(tmp_path) == []

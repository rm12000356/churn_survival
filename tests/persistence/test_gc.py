"""GC / retention tests (ROADMAP Phase 8, Task 8.1 / D-P6)."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from orchestration import gc


def test_prune_mapping_drafts_only_old(tmp_path: Path) -> None:
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    old = drafts / "old.json"
    old.write_text("{}", encoding="utf-8")
    new = drafts / "new.json"
    new.write_text("{}", encoding="utf-8")
    old_ts = (datetime.now(UTC) - timedelta(days=30)).timestamp()
    os.utime(old, (old_ts, old_ts))

    deleted = gc.prune_mapping_drafts(drafts, 7, now=datetime.now(UTC))
    assert deleted == [old]
    assert new.exists()
    assert gc.prune_mapping_drafts(drafts, 0) == []  # disabled


def test_prune_model_artifacts_keeps_newest(tmp_path: Path) -> None:
    base = tmp_path / "models"
    for index in range(3):
        artifact = base / f"m{index}"
        artifact.mkdir(parents=True)
        (artifact / "model.json").write_text("{}", encoding="utf-8")
        # Recency is the sidecar's mtime (rewritten on every save/refit).
        os.utime(artifact / "model.json", (index + 1, index + 1))  # m0 oldest, m2 newest

    deleted = gc.prune_model_artifacts(base, 1)
    assert sorted(path.name for path in deleted) == ["m0", "m1"]
    assert (base / "m2").exists()
    assert gc.prune_model_artifacts(base, 0) == []


def test_prune_model_artifacts_keeps_models_retained_runs_use(tmp_path: Path) -> None:
    base = tmp_path / "models"
    for index in range(3):
        artifact = base / f"m{index}"
        artifact.mkdir(parents=True)
        (artifact / "model.json").write_text("{}", encoding="utf-8")
        os.utime(artifact / "model.json", (index + 1, index + 1))

    deleted = gc.prune_model_artifacts(base, 1, protected={"m0"})
    assert [path.name for path in deleted] == ["m1"]
    assert (base / "m0").exists()


def test_gc_main_flags(tmp_path: Path, capsys) -> None:
    run_dir = tmp_path / "runs"
    code = gc.main(
        [
            "--run-dir",
            str(run_dir),
            "--model-dir",
            str(tmp_path / "models"),
            "--models",
            "1",
            "--runs",
            "1",
            "--pending-ttl-days",
            "0",
            "--recover",
        ]
    )
    assert code == 0
    assert "GC:" in capsys.readouterr().out


def test_gc_main_missing_value_is_usage_error() -> None:
    assert gc.main(["--models"]) == 2

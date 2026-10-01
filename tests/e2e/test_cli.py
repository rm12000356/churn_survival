"""E2E: CLI ``run --persist-run`` and ``gc`` (ROADMAP Task 8.3)."""

from __future__ import annotations

from pathlib import Path

from orchestration.gc import main as gc_main
from orchestration.node import main as run_main


def test_cli_persist_run_and_gc(tmp_path: Path, clean_csv: Path) -> None:
    run_dir = tmp_path / "runs"

    code = run_main([str(clean_csv), "--persist-run", "--run-dir", str(run_dir)])
    assert code == 0
    assert (run_dir / "index.sqlite").is_file()
    run_dirs = [child for child in run_dir.iterdir() if child.is_dir()]
    assert len(run_dirs) == 1
    assert (run_dirs[0] / "summary.json").is_file()

    # TTL 0 avoids pruning the active CONFIG_DIR's committed drafts.
    gc_code = gc_main(
        [
            "--run-dir",
            str(run_dir),
            "--runs",
            "0",
            "--models",
            "0",
            "--pending-ttl-days",
            "0",
            "--recover",
        ]
    )
    assert gc_code == 0


def test_cli_persist_run_without_identity_is_usage_ok(
    tmp_path: Path, clean_csv: Path
) -> None:
    # A normal run always has an identity; persisted dir must contain state.json.
    run_dir = tmp_path / "runs"
    assert run_main([str(clean_csv), "--persist-run", "--run-dir", str(run_dir)]) == 0
    child = next(p for p in run_dir.iterdir() if p.is_dir())
    assert (child / "state.json").is_file()
    assert (child / "node5.json").is_file()
    assert (child / "report.html").is_file()

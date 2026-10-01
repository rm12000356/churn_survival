"""Behavioural tests for the frontend's pure helpers (REVIEW T9).

The static tests elsewhere only assert that strings are present. These run the
real ES modules under Node, so CSV escaping, hash parsing and the stage
mapping are executed, not grepped. Skipped when ``node`` is not installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "frontend" / "static"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _run(script: str) -> object:
    """Run an ES-module script with the static dir's modules importable; return its JSON."""
    prelude = (
        f"const ui = await import({json.dumps((STATIC / 'components' / 'ui.js').as_uri())});\n"
        f"const router = await import({json.dumps((STATIC / 'router.js').as_uri())});\n"
        f"const stages = await import("
        f"{json.dumps((STATIC / 'components' / 'stageTracker.js').as_uri())});\n"
    )
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", prelude + script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=HYPERLINK(\"http://x\")", "\"'=HYPERLINK(\"\"http://x\"\")\""),
        ("+1", "'+1"),
        ("-cmd", "'-cmd"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ("\tlead", "'\tlead"),
        ("a,b", '"a,b"'),
        ("line\rbreak", '"line\rbreak"'),
        ("line\nbreak", '"line\nbreak"'),
        ("plain", "plain"),
        (None, ""),
    ],
)
def test_csv_cell_neutralises_formulas_and_quotes(value: object, expected: str) -> None:
    out = _run(f"console.log(JSON.stringify(ui.csvCell({json.dumps(value)})));")
    assert out == expected


def test_csv_cell_leaves_numbers_unchanged() -> None:
    # A negative number is data, not a formula: only *string* cells are prefixed.
    out = _run("console.log(JSON.stringify([ui.csvCell(-0.5), ui.csvCell(0.731), ui.csvCell(3)]));")
    assert out == ["-0.5", "0.731", "3"]


def test_csv_text_uses_crlf_rows_in_given_order() -> None:
    out = _run(
        'console.log(JSON.stringify(ui.csvText(["rank","id"], [[2,"b"],[1,"a"]])));'
    )
    assert out == "rank,id\r\n2,b\r\n1,a"


def test_parse_hash_keeps_run_id_segment() -> None:
    out = _run('console.log(JSON.stringify(router.parseHash("#/runs/abc123/report?x=1")));')
    assert out == {"segments": ["runs", "abc123", "report"], "params": {"x": "1"}}


@pytest.mark.parametrize(
    ("stage", "index"),
    [("routing", 0), ("node2", 1), ("node3", 2), ("node4", 3), ("done", 4), (None, -1), ("x", -1)],
)
def test_stage_index_maps_api_stage(stage: str | None, index: int) -> None:
    out = _run(f"console.log(JSON.stringify(stages.stageIndex({json.dumps(stage)})));")
    assert out == index

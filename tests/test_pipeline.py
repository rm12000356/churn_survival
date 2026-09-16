from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline.main import IMPLEMENTED_NODES, NODE_NAMES, UnimplementedNodeError, main, run_node


def test_unimplemented_nodes_raise() -> None:
    for node in NODE_NAMES:
        if node in IMPLEMENTED_NODES:
            continue
        with pytest.raises(UnimplementedNodeError):
            run_node(node)


def test_node1_is_implemented() -> None:
    assert "node1" in IMPLEMENTED_NODES
    # Without a raw-data argument, node1's CLI returns usage (exit code 2).
    assert run_node("node1", []) == 2


def test_unknown_node_raises() -> None:
    with pytest.raises(UnimplementedNodeError):
        run_node("node9")


def test_main_returns_nonzero_with_clear_message(
    fresh_settings: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["node4"])
    assert code == 1
    assert "not implemented" in capsys.readouterr().err


def test_main_no_args_returns_usage(
    fresh_settings: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main([])
    assert code == 2
    assert "map" in capsys.readouterr().err


def test_main_map_dispatch(
    fresh_settings: None, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fixtures = Path(__file__).parent / "adapters" / "fixtures"
    out = tmp_path / "draft.json"
    code = main(["map", str(fixtures / "unmapped_export.csv"), "--out", str(out)])
    assert code == 0
    assert out.is_file()
    assert "Draft mapping report" in capsys.readouterr().out


def test_python_dash_m_exits_nonzero() -> None:
    env = dict(os.environ)
    env["REFERENCE_DATE"] = "2026-08-15"
    env["LLM_PROVIDER"] = "none"
    result = subprocess.run(
        [sys.executable, "-m", "pipeline.main", "node4"],
        capture_output=True,
        text=True,
        env=env,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert result.returncode != 0
    assert "not implemented" in result.stderr

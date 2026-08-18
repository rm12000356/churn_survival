from __future__ import annotations

import os
import subprocess
import sys

import pytest

from pipeline.main import NODE_NAMES, UnimplementedNodeError, main, run_node


def test_all_nodes_unimplemented() -> None:
    for node in NODE_NAMES:
        with pytest.raises(UnimplementedNodeError):
            run_node(node)


def test_unknown_node_raises() -> None:
    with pytest.raises(UnimplementedNodeError):
        run_node("node9")


def test_main_returns_nonzero_with_clear_message(
    fresh_settings: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(["node3"])
    assert code == 1
    assert "not implemented" in capsys.readouterr().err


def test_main_no_args_returns_usage(
    fresh_settings: None, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main([])
    assert code == 2
    assert "Usage:" in capsys.readouterr().err


def test_python_dash_m_exits_nonzero() -> None:
    env = dict(os.environ)
    env["REFERENCE_DATE"] = "2026-08-15"
    env["LLM_PROVIDER"] = "none"
    result = subprocess.run(
        [sys.executable, "-m", "pipeline.main", "node2"],
        capture_output=True,
        text=True,
        env=env,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert result.returncode != 0
    assert "not implemented" in result.stderr

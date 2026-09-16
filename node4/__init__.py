"""Node 4 — Synthesis / Ranked Account List (architecture §4, ROADMAP Phase 5).

Deterministically merges Node 2 quantitative survival risk with Node 3
qualitative support signals into a ranked, explainable account list. Plain Python
only: no LangGraph, no LLM authority over score/rank/risk/evidence (D-5).
"""

from __future__ import annotations

from node4.node import run_node4

__all__ = ["run_node4"]

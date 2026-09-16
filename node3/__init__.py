"""Node 3 — Support signal extraction (preprocessing, LLM extraction, aggregation).

Public API: ``preprocess_threads``, ``extract_thread_signals``, ``aggregate_customer``
and ``run_node3`` (the full §3.11 output).
"""

from __future__ import annotations

from node3.aggregate import aggregate_customer
from node3.llm_extractor import extract_thread_signals
from node3.node import run_node3
from node3.preprocess import PreprocessedThread, PreprocessingStats, preprocess_threads

__all__ = [
    "PreprocessedThread",
    "PreprocessingStats",
    "aggregate_customer",
    "extract_thread_signals",
    "preprocess_threads",
    "run_node3",
]

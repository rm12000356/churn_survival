"""Node 3 golden-set quality gate (ROADMAP Task 9.2, architecture §3.10).

CI enforces the §3.10 acceptance bars on the offline deterministic extractor, so
a prompt/extractor regression fails the build. Dataset 7 is a proxy for the
architecture's 150-300 double-annotated real threads; the live-LLM harness
(``scripts/eval_node3_golden.py --live``) remains a manual, pre-change check.
"""

from __future__ import annotations

import hashlib

import pytest

from scripts.eval_node3_golden import (
    EXACT_MATCH_TARGET,
    KAPPA_FLAG_TARGET,
    KAPPA_STRENGTH_TARGET,
    evaluate_golden,
)
from tests.dataset7.test_dataset7 import GOLDEN_SHA256

pytestmark = pytest.mark.golden


def test_dataset7_corpus_golden_hashes() -> None:
    """The gate measures a pinned corpus; drift must fail before measuring."""
    for path, expected in GOLDEN_SHA256.items():
        digest = hashlib.sha256(path.read_bytes()).hexdigest().upper()
        assert digest == expected, f"{path.name} drift - re-run scripts/generate_dataset7.py"


def test_node3_golden_bars_offline() -> None:
    result = evaluate_golden("dataset7", live=False)

    assert result.failures == []
    assert result.passed
    assert result.n_customers > 0
    assert result.kappa_flag_type >= KAPPA_FLAG_TARGET
    assert result.kappa_signal_strength >= KAPPA_STRENGTH_TARGET
    assert result.cancellation_exact_match >= EXACT_MATCH_TARGET
    assert result.renewal_exact_match >= EXACT_MATCH_TARGET

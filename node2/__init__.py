"""Node 2 — Survival model (eligibility, fit/score, fallback, artifact).

Public API: ``fit_model`` / ``score_customers`` (separation is mandatory, §2.2)
and ``run_node2`` (full fit + score -> §2.12 output).
"""

from __future__ import annotations

from node2.artifact import FittedArtifact
from node2.node import fit_model, run_node2, score_customers, score_to_output

__all__ = ["FittedArtifact", "fit_model", "run_node2", "score_customers", "score_to_output"]

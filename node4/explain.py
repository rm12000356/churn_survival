"""Node 4 optional explanation (architecture §4.25, D-5).

**D-5 (v1 scope): no LLM calls in Node 4.** ``explanation`` is always ``None``;
the deterministic result is the sole acceptance path. When LLM polishing is added
in a later task it may only convert already-computed decision information into
text and must never change score / confidence / risk level / rank / reasons /
evidence, nor may its failure block the deterministic output.
"""

from __future__ import annotations


def build_explanation() -> None:
    """Return no explanation in v1 (LLM explanation deferred)."""
    return None

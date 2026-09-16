"""Structured LLM output contract (architecture §5.16).

`extra="forbid"` means any decision field the model attempts to return
(`risk_level`, `rank`, `score`, `confidence`, `recommendation`, ...) is a schema
violation and is rejected, falling back to deterministic templates.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LLMExplanation(BaseModel):
    """The only shape Node 5 accepts from the explainer (§5.16)."""

    model_config = ConfigDict(extra="forbid")

    headline: str
    summary: str
    reason_explanations: list[str] = Field(default_factory=list)

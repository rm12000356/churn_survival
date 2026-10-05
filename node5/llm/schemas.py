from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class LLMExplanation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    headline: str
    summary: str
    reason_explanations: list[str] = Field(default_factory=list)

"""Application configuration via environment + `.env` (architecture §8.6, ROADMAP Task 0.4).

Every setting is read from the environment / `.env`; nothing is hard-coded at runtime.
`REFERENCE_DATE` is a *declared* dataset cut-off and is required — it must never fall
back to "today" at runtime (reproducibility rule, architecture §1.3).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. Missing required keys fail loudly at construction."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Runtime
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    REFERENCE_DATE: date = Field(
        ...,
        description="Declared dataset cut-off date. Required; never defaults to today.",
    )

    # LLM (provider-agnostic; selected at runtime)
    LLM_PROVIDER: Literal["anthropic", "openai", "none"] = "none"
    LLM_API_KEY: str | None = None
    LLM_MODEL: str | None = None

    # Paths
    MODEL_DIR: Path = Path("models/")
    RAW_DATA_DIR: Path = Path("data/raw/")
    PROCESSED_DATA_DIR: Path = Path("data/processed/")
    CONFIG_DIR: Path = Path("config/")

    # Pipeline behavior
    DEFAULT_LOOKBACK_DAYS: int = 365
    MAX_THREADS_PER_CUSTOMER: int = 50
    MAX_MESSAGES_PER_THREAD: int = 100

    @model_validator(mode="after")
    def _require_llm_keys_when_provider_is_set(self) -> Settings:
        if self.LLM_PROVIDER != "none" and not (self.LLM_API_KEY and self.LLM_MODEL):
            raise ValueError(
                "LLM_API_KEY and LLM_MODEL are required when LLM_PROVIDER != 'none'"
            )
        return self


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide Settings singleton (lazy-loaded)."""
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]  # REFERENCE_DATE may come from .env
    return _settings

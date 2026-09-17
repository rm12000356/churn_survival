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

    # Node 3 external sources (multi-source addendum §3/§9). Mock mode is the
    # default and requires no credentials; live mode needs source-specific
    # secrets, which are validated loudly at Settings construction.
    NODE3_SOURCE_MODE: Literal["mock", "live"] = "mock"
    NODE3_MOCK_SOURCES_DIR: Path = Path("mock_sources/")

    X_ENABLED: bool = False
    X_CLIENT_ID: str | None = None
    X_CLIENT_SECRET: str | None = None
    X_ACCESS_TOKEN: str | None = None

    GMAIL_ENABLED: bool = False
    GMAIL_CLIENT_ID: str | None = None
    GMAIL_CLIENT_SECRET: str | None = None
    GMAIL_REFRESH_TOKEN: str | None = None

    @model_validator(mode="after")
    def _require_llm_keys_when_provider_is_set(self) -> Settings:
        if self.LLM_PROVIDER != "none" and not (self.LLM_API_KEY and self.LLM_MODEL):
            raise ValueError("LLM_API_KEY and LLM_MODEL are required when LLM_PROVIDER != 'none'")
        return self

    @model_validator(mode="after")
    def _require_source_credentials_when_enabled(self) -> Settings:
        if self.X_ENABLED and not (
            self.X_CLIENT_ID and self.X_CLIENT_SECRET and self.X_ACCESS_TOKEN
        ):
            raise ValueError(
                "X_CLIENT_ID, X_CLIENT_SECRET and X_ACCESS_TOKEN are required when X_ENABLED=true"
            )
        if self.GMAIL_ENABLED and not (
            self.GMAIL_CLIENT_ID and self.GMAIL_CLIENT_SECRET and self.GMAIL_REFRESH_TOKEN
        ):
            raise ValueError(
                "GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET and GMAIL_REFRESH_TOKEN are "
                "required when GMAIL_ENABLED=true"
            )
        return self


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide Settings singleton (lazy-loaded)."""
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]  # REFERENCE_DATE may come from .env
    return _settings

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "json"
    REFERENCE_DATE: date = Field(
        ...,
        description="Declared dataset cut-off date. Required; never defaults to today.",
    )

    LLM_PROVIDER: Literal["anthropic", "openai", "none"] = "none"
    LLM_API_KEY: str | None = None
    LLM_MODEL: str | None = None

    MODEL_DIR: Path = Path("models/")
    RAW_DATA_DIR: Path = Path("data/raw/")
    PROCESSED_DATA_DIR: Path = Path("data/processed/")
    CONFIG_DIR: Path = Path("config/")
    RUN_DIR: Path = Path("runs/")
    FRONTEND_DIR: Path = Path("frontend/")

    DEFAULT_LOOKBACK_DAYS: int = 365
    MAX_THREADS_PER_CUSTOMER: int = 50
    MAX_MESSAGES_PER_THREAD: int = 100

    MODEL_RETENTION_MAX: int = 0
    RUN_RETENTION_MAX: int = 0
    PENDING_RUN_TTL_DAYS: int = 7
    GC_ON_STARTUP: bool = False

    API_KEY: str | None = None
    API_KEY_HEADER: str = "X-API-Key"
    API_REQUIRE_KEY_FOR_READS: bool = False
    API_ENABLE_WRITES: bool = False
    API_ALLOW_ARBITRARY_PATHS: bool = False
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000
    RUN_MAX_WORKERS: int = Field(default=2, ge=1, le=16)
    RUN_MAX_QUEUED: int = Field(default=16, ge=1, le=1000)
    RUN_TRIGGERS_PER_MINUTE: int = Field(default=30, ge=0)
    UPLOADS_PER_MINUTE: int = Field(default=20, ge=0)

    NODE3_LLM_MAX_CONCURRENCY: int | None = Field(default=None, ge=1, le=64)
    NODE5_LLM_MAX_CONCURRENCY: int | None = Field(default=None, ge=1, le=32)

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

    @model_validator(mode="after")
    def _require_api_key_when_writes_enabled(self) -> Settings:
        if self.API_ENABLE_WRITES and not self.API_KEY:
            raise ValueError(
                "API_KEY is required when API_ENABLE_WRITES=true: mapping confirmation "
                "must never be reachable through an unauthenticated endpoint"
            )
        return self

    @model_validator(mode="after")
    def _require_api_key_when_reads_protected(self) -> Settings:
        if self.API_REQUIRE_KEY_FOR_READS and not self.API_KEY:
            raise ValueError(
                "API_KEY is required when API_REQUIRE_KEY_FOR_READS=true: read protection "
                "cannot be enforced without a key, so reads would silently stay open"
            )
        return self


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]  # REFERENCE_DATE may come from .env
    return _settings

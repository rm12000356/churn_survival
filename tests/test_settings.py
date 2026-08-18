from __future__ import annotations

import pytest
from pydantic import ValidationError

from config.settings import Settings


def test_missing_reference_date_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REFERENCE_DATE", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_valid_settings_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFERENCE_DATE", "2026-08-15")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("LLM_PROVIDER", "none")
    settings = Settings(_env_file=None)
    assert settings.REFERENCE_DATE.isoformat() == "2026-08-15"
    assert settings.LOG_LEVEL == "DEBUG"
    assert settings.LLM_PROVIDER == "none"
    assert settings.LLM_API_KEY is None


def test_llm_provider_requires_key_and_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFERENCE_DATE", "2026-08-15")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    with pytest.raises(ValueError):
        Settings(_env_file=None)


def test_invalid_reference_date_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFERENCE_DATE", "not-a-date")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)

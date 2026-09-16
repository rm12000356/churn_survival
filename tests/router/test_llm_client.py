from __future__ import annotations

import sys

import pytest

from router.llm_mapper import LlmClient, MappingReportError, create_llm_client


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _FakeHttpx:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.post_urls: list[str] = []
        self.post_payloads: list[dict] = []

    def post(self, url: str, json: dict, headers: dict, timeout: float) -> _FakeResponse:
        self.post_urls.append(url)
        self.post_payloads.append(json)
        return _FakeResponse(self._payload)


def _install_fake_httpx(monkeypatch, payload: dict) -> _FakeHttpx:
    fake = _FakeHttpx(payload)
    monkeypatch.setitem(sys.modules, "httpx", fake)
    return fake


def test_complete_openai_path(monkeypatch) -> None:
    fake = _install_fake_httpx(monkeypatch, {"choices": [{"message": {"content": "OPENAI_OK"}}]})
    client = LlmClient(provider="openai", model="gpt-4o", api_key="sk-test")
    assert client.complete("prompt") == "OPENAI_OK"
    assert fake.post_urls[0].startswith("https://api.openai.com/v1/chat/completions")


def test_complete_anthropic_path(monkeypatch) -> None:
    fake = _install_fake_httpx(monkeypatch, {"content": [{"text": "ANTHROPIC_OK"}]})
    client = LlmClient(provider="anthropic", model="claude-sonnet-4", api_key="sk-ant-test")
    assert client.complete("prompt") == "ANTHROPIC_OK"
    assert fake.post_urls[0].startswith("https://api.anthropic.com/v1/messages")


def test_complete_unsupported_provider_raises(monkeypatch) -> None:
    _install_fake_httpx(monkeypatch, {})
    client = LlmClient(provider="gemini", model="x", api_key="k")
    with pytest.raises(MappingReportError, match="unsupported LLM_PROVIDER"):
        client.complete("prompt")


def test_complete_uses_custom_base_url(monkeypatch) -> None:
    fake = _install_fake_httpx(monkeypatch, {"choices": [{"message": {"content": "ok"}}]})
    client = LlmClient(
        provider="openai",
        model="gpt-4o",
        api_key="k",
        base_url="https://proxy.example.com/v1",
    )
    client.complete("prompt")
    assert fake.post_urls[0].startswith("https://proxy.example.com/v1")


def test_complete_defaults_temperature_to_02(monkeypatch) -> None:
    fake = _install_fake_httpx(monkeypatch, {"choices": [{"message": {"content": "ok"}}]})
    client = LlmClient(provider="openai", model="gpt-4o", api_key="k")
    client.complete("prompt")
    assert fake.post_payloads[0]["temperature"] == 0.2


def test_complete_uses_explicit_temperature(monkeypatch) -> None:
    fake = _install_fake_httpx(monkeypatch, {"content": [{"text": "ok"}]})
    client = LlmClient(provider="anthropic", model="claude", api_key="k")
    client.complete("prompt", temperature=0.05)
    assert fake.post_payloads[0]["temperature"] == 0.05


def _set_provider(fresh_settings, monkeypatch, provider: str, key: str | None, model: str | None):
    import config.settings as cs

    monkeypatch.setenv("LLM_PROVIDER", provider)
    if key is None:
        monkeypatch.delenv("LLM_API_KEY", raising=False)
    else:
        monkeypatch.setenv("LLM_API_KEY", key)
    if model is None:
        monkeypatch.delenv("LLM_MODEL", raising=False)
    else:
        monkeypatch.setenv("LLM_MODEL", model)
    monkeypatch.setattr(cs, "_settings", None)


def test_create_llm_client_requires_provider(fresh_settings, monkeypatch) -> None:
    _set_provider(fresh_settings, monkeypatch, "none", "k", "m")
    with pytest.raises(MappingReportError, match="LLM_PROVIDER=none"):
        create_llm_client()


def test_create_llm_client_requires_credentials(fresh_settings, monkeypatch) -> None:
    # Settings already validates credentials when LLM_PROVIDER != none, so this
    # branch is defence-in-depth: bypass validation via a stub get_settings().
    from types import SimpleNamespace

    monkeypatch.setattr(
        "config.settings.get_settings",
        lambda: SimpleNamespace(
            LLM_PROVIDER="openai", LLM_API_KEY="", LLM_MODEL="", REFERENCE_DATE=None
        ),
    )
    with pytest.raises(MappingReportError, match="LLM_API_KEY and LLM_MODEL"):
        create_llm_client()


def test_create_llm_client_builds_client(fresh_settings, monkeypatch) -> None:
    _set_provider(fresh_settings, monkeypatch, "anthropic", "sk-ant-test", "claude-sonnet-4")
    client = create_llm_client()
    assert isinstance(client, LlmClient)
    assert client.provider == "anthropic"
    assert client.model == "claude-sonnet-4"
    assert client.api_key == "sk-ant-test"

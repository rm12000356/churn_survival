from __future__ import annotations

import json
import threading
from collections.abc import Callable

import httpx
import pytest

import router.llm_mapper as llm_mapper
from router.llm_mapper import LlmClient, MappingReportError, create_llm_client


class _FakeHttpx:
    """Records requests served by an ``httpx.MockTransport`` behind the shared client."""

    def __init__(self, respond: Callable[[int], httpx.Response]) -> None:
        self._respond = respond
        self.post_urls: list[str] = []
        self.post_payloads: list[dict] = []
        self.sleeps: list[float] = []
        self.client = httpx.Client(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.post_urls.append(str(request.url))
        self.post_payloads.append(json.loads(request.content))
        return self._respond(len(self.post_urls))


def _install_transport(
    monkeypatch, respond: Callable[[int], httpx.Response]
) -> _FakeHttpx:
    fake = _FakeHttpx(respond)
    monkeypatch.setattr(llm_mapper, "_shared_http_client", lambda: fake.client)
    monkeypatch.setattr(llm_mapper, "_sleep", fake.sleeps.append)
    return fake


def _install_fake_httpx(monkeypatch, payload: dict) -> _FakeHttpx:
    return _install_transport(monkeypatch, lambda _n: httpx.Response(200, json=payload))


_OK = {"choices": [{"message": {"content": "ok"}}]}


def test_retries_429_honouring_retry_after(monkeypatch) -> None:
    fake = _install_transport(
        monkeypatch,
        lambda n: httpx.Response(429, headers={"Retry-After": "2"})
        if n == 1
        else httpx.Response(200, json=_OK),
    )
    client = LlmClient(provider="openai", model="gpt-4o", api_key="k")
    assert client.complete("prompt") == "ok"
    assert len(fake.post_urls) == 2
    assert fake.sleeps == [2.0]


def test_retries_5xx_then_raises_after_max_attempts(monkeypatch) -> None:
    fake = _install_transport(monkeypatch, lambda _n: httpx.Response(503))
    client = LlmClient(provider="openai", model="gpt-4o", api_key="k")
    with pytest.raises(httpx.HTTPStatusError):
        client.complete("prompt")
    assert len(fake.post_urls) == llm_mapper._MAX_ATTEMPTS
    assert len(fake.sleeps) == llm_mapper._MAX_ATTEMPTS - 1


def test_client_error_is_not_retried(monkeypatch) -> None:
    fake = _install_transport(monkeypatch, lambda _n: httpx.Response(400))
    client = LlmClient(provider="openai", model="gpt-4o", api_key="k")
    with pytest.raises(httpx.HTTPStatusError):
        client.complete("prompt")
    assert len(fake.post_urls) == 1
    assert fake.sleeps == []


def test_transport_error_is_retried(monkeypatch) -> None:
    def respond(n: int) -> httpx.Response:
        if n == 1:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json=_OK)

    fake = _install_transport(monkeypatch, respond)
    client = LlmClient(provider="openai", model="gpt-4o", api_key="k")
    assert client.complete("prompt") == "ok"
    assert len(fake.post_urls) == 2
    assert len(fake.sleeps) == 1


def test_shared_http_client_is_reused_and_thread_safe(monkeypatch) -> None:
    monkeypatch.setattr(llm_mapper, "_http_client", None)
    seen: list[object] = []

    def grab() -> None:
        seen.append(llm_mapper._shared_http_client())

    workers = [threading.Thread(target=grab) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert len({id(client) for client in seen}) == 1
    shared = seen[0]
    shared.close()  # type: ignore[attr-defined]
    assert llm_mapper._shared_http_client() is not shared  # recreated after close
    llm_mapper._shared_http_client().close()


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

"""Source configuration, registry, and CLI selection tests (addendum §3/§9/§10)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from config.models import IdentityMappingConfig, Node3SourcesConfig
from config.settings import Settings, get_settings
from node3.node import _customers_from_mapping, _select_sources
from node3.node import main as node3_main
from node3.sources.errors import SourceError, SourceNotConfiguredError
from node3.sources.gmail_source import MockGmailSource
from node3.sources.registry import agent_identities_by_source, build_sources
from node3.sources.x_source import MockXSource
from tests.node3.conftest import thread


def test_load_sources_config(sources_config: Node3SourcesConfig) -> None:
    assert sources_config.sources_version == "sources_v1.0"
    assert sources_config.identity_mapping_version == "1"
    assert set(sources_config.sources) == {"x", "gmail"}
    assert sources_config.sources["x"].include_dms is True


def test_agent_identities_by_source(sources_config: Node3SourcesConfig) -> None:
    identities = agent_identities_by_source(sources_config)
    assert identities["x"] == ["acme_support"]
    assert identities["gmail"] == ["support@acme.com"]


def test_registry_builds_mock_sources(
    sources_config: Node3SourcesConfig, fresh_settings: None
) -> None:
    sources = build_sources(sources_config, settings=get_settings())
    assert len(sources) == 2
    assert all(isinstance(s, MockXSource | MockGmailSource) for s in sources)


def test_registry_unknown_source_raises(sources_config: Node3SourcesConfig) -> None:
    bad = sources_config.model_copy(
        update={
            "sources": {
                **sources_config.sources,
                "myspace": sources_config.sources["x"],
            }
        }
    )
    with pytest.raises(SourceError, match="unknown external source"):
        build_sources(bad)


def test_registry_live_without_enable_fails_loudly(
    sources_config: Node3SourcesConfig, fresh_settings: None
) -> None:
    x_spec = sources_config.sources["x"].model_copy(update={"mode": "live"})
    live = sources_config.model_copy(
        update={"sources": {**sources_config.sources, "x": x_spec}}
    )
    with pytest.raises(SourceNotConfiguredError, match="X_ENABLED=false"):
        build_sources(live, settings=get_settings())


def test_registry_live_gmail_without_enable_fails_loudly(
    sources_config: Node3SourcesConfig, fresh_settings: None
) -> None:
    gmail_spec = sources_config.sources["gmail"].model_copy(update={"mode": "live"})
    live = sources_config.model_copy(
        update={"sources": {**sources_config.sources, "gmail": gmail_spec}}
    )
    with pytest.raises(SourceNotConfiguredError, match="GMAIL_ENABLED=false"):
        build_sources(live, settings=get_settings())


def test_registry_builds_live_x_with_credentials(
    sources_config: Node3SourcesConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("REFERENCE_DATE", "2026-08-15")
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.setenv("X_ENABLED", "true")
    monkeypatch.setenv("X_CLIENT_ID", "client")
    monkeypatch.setenv("X_CLIENT_SECRET", "secret")
    monkeypatch.setenv("X_ACCESS_TOKEN", "token")
    import config.settings as cs

    monkeypatch.setattr(cs, "_settings", None)
    x_spec = sources_config.sources["x"].model_copy(update={"mode": "live", "enabled": True})
    live = sources_config.model_copy(update={"sources": {"x": x_spec}})
    sources = build_sources(live, settings=cs.get_settings())
    from node3.sources.x_source import XSource

    assert isinstance(sources[0], XSource)


def test_settings_require_source_credentials_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("REFERENCE_DATE", "2026-08-15")
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.setenv("X_ENABLED", "true")
    monkeypatch.delenv("X_ACCESS_TOKEN", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_select_sources_filters(sources_config: Node3SourcesConfig) -> None:
    selected = _select_sources(sources_config, "x", "live")
    assert set(selected.sources) == {"x"}
    assert selected.sources["x"].mode == "live"


def test_select_sources_mock_shorthand(sources_config: Node3SourcesConfig) -> None:
    selected = _select_sources(sources_config, "mock", None)
    assert set(selected.sources) == {"x", "gmail"}


def test_select_sources_unknown_raises(sources_config: Node3SourcesConfig) -> None:
    with pytest.raises(KeyError):
        _select_sources(sources_config, "myspace", None)


def test_customers_from_mapping(identity_mapping: IdentityMappingConfig) -> None:
    assert _customers_from_mapping(identity_mapping) == [
        "CUST-A",
        "CUST-B",
        "CUST-D",
        "CUST-F",
        "CUST-C",
        "CUST-G",
    ]


def test_cli_source_mode_mock(fresh_settings: None, tmp_path: Path) -> None:
    out_path = tmp_path / "out.json"
    code = node3_main(["--sources", "mock", "--config", "1", "--output", str(out_path)])
    assert code == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["processing_report"]["n_customers_requested"] == 6


def test_cli_threads_and_sources_merge(fresh_settings: None, tmp_path: Path) -> None:
    threads_path = tmp_path / "threads.json"
    threads_path.write_text(
        json.dumps([thread("T-extra", "CUST-X")]), encoding="utf-8"
    )
    out_path = tmp_path / "out.json"
    code = node3_main(
        [
            str(threads_path),
            "--sources",
            "x,gmail",
            "--customers",
            str(_write_customers(tmp_path)),
            "--output",
            str(out_path),
        ]
    )
    assert code == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["processing_report"]["n_customers_requested"] == 7


def test_cli_unknown_source_fails_loudly(
    fresh_settings: None, tmp_path: Path
) -> None:
    code = node3_main(["--sources", "myspace", "--config", "1"])
    assert code == 1


def _write_customers(tmp_path: Path) -> Path:
    path = tmp_path / "customers.json"
    path.write_text(
        json.dumps(["CUST-A", "CUST-B", "CUST-C", "CUST-D", "CUST-F", "CUST-G", "CUST-X"]),
        encoding="utf-8",
    )
    return path

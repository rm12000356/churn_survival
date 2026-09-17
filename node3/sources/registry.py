"""Build external sources from versioned config + environment (addendum §3/§10).

Adding a new source means adding a branch here and a ``SourceSpec`` entry — never
touching Node 3's signal extraction. Live sources validate their credentials at
construction time and fail loudly when misconfigured.

Construction is isolated per source in ``build_sources_safe`` (QA F-5): a failure
building one source (missing directory, live credentials absent, unknown source)
is recorded as a structured error and the remaining sources still run. The
lower-level ``build_sources`` keeps its all-or-nothing raising contract for
direct callers and tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from config.models import Node3SourcesConfig, SourceSpec
from config.settings import Settings, get_settings
from node3.sources.base import ExternalSource
from node3.sources.errors import (
    SourceError,
    SourceNotConfiguredError,
    redact_secrets,
)
from node3.sources.gmail_source import GmailSource, MockGmailSource
from node3.sources.x_source import MockXSource, XSource


@dataclass
class SourceBuildResult:
    """Per-source construction outcome (QA F-5)."""

    sources: list[ExternalSource] = field(default_factory=list)
    errors: list[dict[str, object]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def agent_identities_by_source(config: Node3SourcesConfig) -> dict[str, list[str]]:
    """Declared company/agent identities per source (used by identity resolution)."""
    return {
        name: list(spec.agent_identities)
        for name, spec in config.sources.items()
        if spec.agent_identities
    }


def _mock_dir(spec_dir: str | None, root: Path, name: str) -> Path:
    return Path(spec_dir) if spec_dir else root / name


def _build_one(name: str, spec: SourceSpec, resolved: Settings) -> ExternalSource:
    """Instantiate a single source; raises ``SourceError`` on any failure."""
    mode = spec.mode or resolved.NODE3_SOURCE_MODE
    mock_dir = _mock_dir(spec.mock_dir, Path(resolved.NODE3_MOCK_SOURCES_DIR), name)

    if name == "x":
        if mode == "live":
            if not resolved.X_ENABLED:
                raise SourceNotConfiguredError(
                    "X source is configured in live mode but X_ENABLED=false"
                )
            return XSource(
                access_token=resolved.X_ACCESS_TOKEN,
                include_public=spec.include_public,
                include_dms=spec.include_dms,
                agent_identities=spec.agent_identities,
            )
        return MockXSource(
            mock_dir,
            include_public=spec.include_public,
            include_dms=spec.include_dms,
            agent_identities=spec.agent_identities,
        )
    if name == "gmail":
        if mode == "live":
            if not resolved.GMAIL_ENABLED:
                raise SourceNotConfiguredError(
                    "Gmail source is configured in live mode but GMAIL_ENABLED=false"
                )
            return GmailSource(
                client_id=resolved.GMAIL_CLIENT_ID,
                client_secret=resolved.GMAIL_CLIENT_SECRET,
                refresh_token=resolved.GMAIL_REFRESH_TOKEN,
                agent_identities=spec.agent_identities,
            )
        return MockGmailSource(mock_dir, agent_identities=spec.agent_identities)
    raise SourceError(
        f"unknown external source {name!r}; register it in node3/sources/registry.py"
    )


def _secrets(resolved: Settings) -> tuple[str | None, ...]:
    return (
        resolved.X_CLIENT_SECRET,
        resolved.X_ACCESS_TOKEN,
        resolved.GMAIL_CLIENT_SECRET,
        resolved.GMAIL_REFRESH_TOKEN,
    )


def build_sources(
    config: Node3SourcesConfig, *, settings: Settings | None = None
) -> list[ExternalSource]:
    """Instantiate every enabled source, failing loudly on the first error.

    The per-source ``mode`` falls back to ``NODE3_SOURCE_MODE``. Mock mode never
    needs credentials; live mode requires the corresponding ``*_ENABLED`` flag and
    secrets in the environment.
    """
    resolved = settings or get_settings()
    sources: list[ExternalSource] = []
    for name, spec in sorted(config.sources.items()):
        if not spec.enabled:
            continue
        sources.append(_build_one(name, spec, resolved))
    return sources


def build_sources_safe(
    config: Node3SourcesConfig, *, settings: Settings | None = None
) -> SourceBuildResult:
    """Instantiate every enabled source, isolating per-source construction failures.

    A failure building one source is recorded as a structured ``SOURCE_INIT_FAILED``
    error (secrets redacted) and the remaining sources are still built — never a
    silent mock fallback and never an empty-but-successful source (QA F-5).
    """
    resolved = settings or get_settings()
    secrets = _secrets(resolved)
    result = SourceBuildResult()
    for name, spec in sorted(config.sources.items()):
        if not spec.enabled:
            continue
        try:
            result.sources.append(_build_one(name, spec, resolved))
        except SourceError as exc:
            detail = redact_secrets(str(exc), secrets)
            result.errors.append(
                {"source": name, "code": "SOURCE_INIT_FAILED", "detail": detail}
            )
            result.warnings.append(f"source {name!r} failed to initialize: {detail}")
    return result

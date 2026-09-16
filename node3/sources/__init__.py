"""Node 3 external source adapters (multi-source ingestion, addendum §4)."""

from node3.sources.base import ExternalSource, build_external_message
from node3.sources.errors import (
    IdentityMappingError,
    SourceAPIError,
    SourceAuthError,
    SourceDataError,
    SourceError,
    SourceNotConfiguredError,
    SourceNotImplementedError,
)
from node3.sources.gmail_source import GmailSource, MockGmailSource
from node3.sources.identity import IdentityResolution, resolve_identities
from node3.sources.normalize import NormalizationResult, namespace_id, normalize_threads
from node3.sources.registry import (
    SourceBuildResult,
    agent_identities_by_source,
    build_sources,
    build_sources_safe,
)
from node3.sources.x_source import MockXSource, XSource

__all__ = [
    "ExternalSource",
    "GmailSource",
    "IdentityMappingError",
    "IdentityResolution",
    "MockGmailSource",
    "MockXSource",
    "NormalizationResult",
    "SourceAPIError",
    "SourceAuthError",
    "SourceBuildResult",
    "SourceDataError",
    "SourceError",
    "SourceNotConfiguredError",
    "SourceNotImplementedError",
    "XSource",
    "agent_identities_by_source",
    "build_external_message",
    "build_sources",
    "build_sources_safe",
    "namespace_id",
    "normalize_threads",
    "resolve_identities",
]

"""External-source error hierarchy (multi-source addendum §4/§18).

Every failure mode is explicit and structured: authentication, API, missing
configuration, malformed source data, and identity mapping. Sources never
attempt a workaround when authorization is missing — they fail loudly.
"""

from __future__ import annotations

from collections.abc import Iterable


def redact_secrets(text: str, secrets: Iterable[str | None]) -> str:
    """Remove known credential values from a message before it is reported.

    Source API errors can echo request context (including tokens). Every
    explicitly-supplied non-empty secret is replaced with ``***`` regardless of
    length — the security boundary must not depend on an arbitrary minimum length
    (QA F-10). Blank/``None`` values are ignored. Replacement order is
    deterministic (longest first, then lexicographic) so overlapping secrets
    cannot produce order-dependent output.
    """
    redacted = text
    unique = {secret for secret in secrets if secret}
    for secret in sorted(unique, key=lambda value: (-len(value), value)):
        redacted = redacted.replace(secret, "***")
    return redacted


class SourceError(RuntimeError):
    """Base class for every external-source failure."""


class SourceNotConfiguredError(SourceError):
    """Raised when required credentials/configuration are absent.

    The message never contains secret values — only the names of missing
    settings.
    """


class SourceAuthError(SourceError):
    """Raised when a source rejects or cannot obtain authentication."""


class SourceAPIError(SourceError):
    """Raised when a source API call fails after configuration is valid."""


class SourceNotImplementedError(SourceError):
    """Raised by live adapters whose remote integration is intentionally deferred.

    The class structure, credential validation, and normalized output contract
    are in place; the concrete HTTP/OAuth transport is a documented follow-up.
    """


class SourceDataError(SourceError):
    """Raised when source payloads cannot be parsed into normalized messages."""


class IdentityMappingError(SourceError):
    """Raised when the identity mapping configuration is invalid."""

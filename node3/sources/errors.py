from __future__ import annotations

from collections.abc import Iterable


def redact_secrets(text: str, secrets: Iterable[str | None]) -> str:
    redacted = text
    unique = {secret for secret in secrets if secret}
    for secret in sorted(unique, key=lambda value: (-len(value), value)):
        redacted = redacted.replace(secret, "***")
    return redacted


class SourceError(RuntimeError):
    ...


class SourceNotConfiguredError(SourceError):
    ...


class SourceAuthError(SourceError):
    ...


class SourceAPIError(SourceError):
    ...


class SourceNotImplementedError(SourceError):
    ...


class SourceDataError(SourceError):
    ...


class IdentityMappingError(SourceError):
    ...

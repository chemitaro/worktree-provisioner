"""Helpers for keeping externally visible command diagnostics safe and bounded."""

from __future__ import annotations

import re
from typing import Final

DIAGNOSTIC_LIMIT: Final[int] = 4096

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?P<prefix>[A-Z0-9_.-]*(?:TOKEN|PASSWORD|SECRET|AUTHORIZATION|API[_-]?KEY)[A-Z0-9_.-]*\s*[:=]\s*)(?P<value>[^\s,;]+)"
)
_AUTHORIZATION_HEADER = re.compile(r"(?im)(?P<prefix>\bAuthorization\b\s*:\s*)(?:Bearer\s+)?[^\r\n]+")
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[^\s,;]+")


def bounded(value: str, *, limit: int = DIAGNOSTIC_LIMIT) -> str:
    """Return ``value`` with a hard character limit."""

    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    suffix = "...[truncated]"
    if limit <= len(suffix):
        return suffix[:limit]
    return value[: limit - len(suffix)] + suffix


def redact_secrets(value: str) -> str:
    """Redact common credential-shaped values from command output."""

    redacted = _SECRET_ASSIGNMENT.sub(r"\g<prefix>[REDACTED]", value)
    redacted = _AUTHORIZATION_HEADER.sub(r"\g<prefix>[REDACTED]", redacted)
    return _BEARER_TOKEN.sub("Bearer [REDACTED]", redacted)


def safe_bounded(value: str, *, limit: int = DIAGNOSTIC_LIMIT) -> str:
    """Redact sensitive values before applying the external size bound."""

    return bounded(redact_secrets(value), limit=limit)

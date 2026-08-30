"""Encoding helpers for filesystem and subprocess boundaries."""

from __future__ import annotations

import os


def restore_utf8_surrogates(value: str) -> str:
    """Restore valid UTF-8 bytes represented by filesystem surrogates.

    A process started under the C locale may decode filesystem bytes with
    ``surrogateescape``. Reconstructing and decoding only valid UTF-8 makes
    those values readable while leaving genuinely non-UTF-8 bytes reversible.
    """

    if not any(0xDC80 <= ord(character) <= 0xDCFF for character in value):
        return value
    try:
        return value.encode("utf-8", errors="surrogateescape").decode("utf-8", errors="surrogateescape")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return value


def filesystem_argument(value: str) -> str | bytes:
    """Return an argv value encodable under the active filesystem locale."""

    try:
        os.fsencode(value)
    except UnicodeEncodeError:
        return value.encode("utf-8", errors="surrogateescape")
    return value


__all__ = ["filesystem_argument", "restore_utf8_surrogates"]

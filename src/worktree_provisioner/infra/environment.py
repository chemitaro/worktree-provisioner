"""Process-environment adapter for the application ports.

The adapter intentionally has no product policy.  In particular, it does
not select a root variable or provide compatibility with another product;
the application/CLI layer owns that decision.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EnvironmentAdapter:
    """Expose the process environment through the narrow gateway port."""

    def getenv(self, name: str) -> str | None:
        """Return one environment value without applying any policy."""

        return os.environ.get(name)


# Names kept explicit at the infrastructure boundary for composition roots
# and callers that use either the operating-system or CLI terminology.
OsEnvironmentGateway = EnvironmentAdapter
EnvironmentCliGateway = EnvironmentAdapter
EnvironmentGateway = EnvironmentAdapter

__all__ = [
    "EnvironmentAdapter",
    "EnvironmentCliGateway",
    "EnvironmentGateway",
    "OsEnvironmentGateway",
]

from __future__ import annotations

import pytest

from worktree_provisioner.diagnostics import bounded


@pytest.mark.parametrize("limit", [-3, 0, 1, 2, 3, 5, 13, 14, 15, 32])
def test_bounded_never_exceeds_nonnegative_limit(limit: int) -> None:
    result = bounded("x" * 64, limit=limit)

    assert len(result) <= max(limit, 0)

from __future__ import annotations

import os

from worktree_provisioner.infra.environment import EnvironmentAdapter


def test_environment_gateway_returns_only_the_requested_process_value(monkeypatch) -> None:
    monkeypatch.setenv("WORKTREE_PROVISIONER_ROOT", "/tmp/worktrees")

    gateway = EnvironmentAdapter()

    assert gateway.getenv("WORKTREE_PROVISIONER_ROOT") == "/tmp/worktrees"
    assert gateway.getenv("MISSING_ENVIRONMENT_VALUE") is None
    assert os.environ.get("WORKTREE_PROVISIONER_ROOT") == gateway.getenv("WORKTREE_PROVISIONER_ROOT")

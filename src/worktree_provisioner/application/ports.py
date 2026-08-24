"""Slim application ports used by concrete infrastructure adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from worktree_provisioner.application.contracts import (
    BootstrapResult,
    GitWorktreeRecord,
)


class GitGateway(Protocol):
    def resolve_checkout_root(self, path: Path) -> Path: ...

    def current_branch_or_none(self, repo_root: Path) -> str | None: ...

    def local_branch_exists(self, repo_root: Path, branch: str) -> bool: ...

    def check_branch_ref(self, repo_root: Path, branch: str) -> bool: ...

    def worktree_list(self, repo_root: Path) -> list[GitWorktreeRecord]: ...

    def add_worktree(self, repo_root: Path, *, path: Path, branch: str) -> None: ...

    def remove_worktree(self, repo_root: Path, *, path: Path, force: bool) -> None: ...


class BootstrapGateway(Protocol):
    def run_make_init_if_available(self, worktree_path: Path) -> BootstrapResult: ...


class FilesystemGateway(Protocol):
    def lstat_kind(self, path: Path) -> str: ...

    def path_exists_no_follow(self, path: Path) -> bool: ...

    def ensure_directory(self, path: Path) -> None: ...

    def remove_target_no_follow(self, path: Path) -> None: ...


class EnvironmentGateway(Protocol):
    def getenv(self, name: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class ApplicationPorts:
    """Concrete-adapter bundle assembled at the application boundary."""

    git: GitGateway
    bootstrap: BootstrapGateway
    filesystem: FilesystemGateway
    environment: EnvironmentGateway


# Short alias for callers that use the generic term from the composition root.
Ports = ApplicationPorts


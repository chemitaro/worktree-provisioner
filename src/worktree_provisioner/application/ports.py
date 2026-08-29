"""Slim application ports used by concrete infrastructure adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeAlias

from worktree_provisioner.application.contracts import (
    BootstrapResult,
    GitWorktreeRecord,
)

PathIdentity: TypeAlias = tuple[int, int]


class DirectoryCapability(Protocol):
    """Descriptor-bound directory capability used for mutation operations."""

    fd: int
    path: Path

    def __enter__(self) -> DirectoryCapability: ...

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None: ...

    def bind_root(self, root: DirectoryCapability) -> None: ...

    def is_within_bound_root(self) -> bool: ...

    def path_identity_matches(self) -> bool: ...


class GitGateway(Protocol):
    def resolve_checkout_root(self, path: Path) -> Path: ...

    def current_branch_or_none(self, repo_root: Path) -> str | None: ...

    def local_branch_exists(self, repo_root: Path, branch: str) -> bool: ...

    def check_branch_ref(self, repo_root: Path, branch: str) -> bool: ...

    def worktree_list(self, repo_root: Path) -> list[GitWorktreeRecord]: ...

    def add_worktree(self, repo_root: Path, *, path: Path, branch: str) -> None: ...

    def add_worktree_bound(
        self, repo_root: Path, *, directory: DirectoryCapability, name: str, branch: str
    ) -> None: ...

    def remove_worktree(self, repo_root: Path, *, path: Path, force: bool) -> None: ...

    def remove_worktree_bound(
        self, repo_root: Path, *, directory: DirectoryCapability, name: str, force: bool
    ) -> None: ...


class BootstrapGateway(Protocol):
    def run_make_init_if_available(self, worktree_path: Path) -> BootstrapResult: ...


class FilesystemGateway(Protocol):
    def lstat_kind(self, path: Path) -> str: ...

    def path_exists_no_follow(self, path: Path) -> bool: ...

    def path_identity_no_follow(self, path: Path) -> PathIdentity | None: ...

    def ensure_directory(self, path: Path) -> None: ...

    def remove_target_no_follow(self, path: Path) -> None: ...

    def open_directory(self, path: Path) -> DirectoryCapability: ...

    def remove_target_no_follow_bound(self, directory: DirectoryCapability, target: Path) -> None: ...


class EnvironmentGateway(Protocol):
    def getenv(self, name: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class ApplicationPorts:
    """Concrete-adapter bundle assembled at the application boundary."""

    git: GitGateway
    bootstrap: BootstrapGateway
    filesystem: FilesystemGateway
    environment: EnvironmentGateway

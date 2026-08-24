"""No-follow filesystem adapter for worktree artifacts."""

from __future__ import annotations

import shutil
import stat
from dataclasses import dataclass
from pathlib import Path


class FilesystemAdapterError(RuntimeError):
    """Typed failure for an unsafe or failed filesystem operation."""

    __slots__ = ("kind", "operation", "path")

    operation: str
    path: Path
    kind: str | None

    def __init__(self, *, operation: str, path: Path, message: str, kind: str | None = None) -> None:
        self.operation = operation
        self.path = path
        self.kind = kind
        super().__init__(message)


FilesystemError = FilesystemAdapterError
FilesystemGatewayError = FilesystemAdapterError


@dataclass(frozen=True, slots=True)
class FilesystemCliGateway:
    """Implement the application filesystem port without following targets."""

    def lstat_kind(self, path: Path) -> str:
        target = _validated_path(path)
        try:
            info = target.lstat()
        except FileNotFoundError:
            return "missing"
        except (PermissionError, OSError) as exc:
            raise _wrapped_error("lstat_kind", target, exc) from exc
        return _kind_from_mode(info.st_mode)

    def path_exists_no_follow(self, path: Path) -> bool:
        return self.lstat_kind(path) != "missing"

    def ensure_directory(self, path: Path) -> None:
        target = _validated_path(path)
        kind = self.lstat_kind(target)
        if kind == "directory":
            return
        if kind != "missing":
            raise FilesystemAdapterError(
                operation="ensure_directory",
                path=target,
                kind=kind,
                message=f"cannot use {kind} as a directory: {target}",
            )

        try:
            target.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError) as exc:
            raise _wrapped_error("ensure_directory", target, exc) from exc

        # ``mkdir(..., exist_ok=True)`` may race with another creator.  The
        # postcondition is checked with lstat, so a symlink is never treated as
        # the requested directory.
        final_kind = self.lstat_kind(target)
        if final_kind != "directory":
            raise FilesystemAdapterError(
                operation="ensure_directory",
                path=target,
                kind=final_kind,
                message=f"directory creation produced {final_kind}: {target}",
            )

    def remove_target_no_follow(self, path: Path) -> None:
        target = _validated_path(path)
        kind = self.lstat_kind(target)
        if kind == "missing":
            raise FilesystemAdapterError(
                operation="remove_target_no_follow",
                path=target,
                kind=kind,
                message=f"cleanup target is missing: {target}",
            )
        if kind == "other":
            raise FilesystemAdapterError(
                operation="remove_target_no_follow",
                path=target,
                kind=kind,
                message=f"cleanup target is a special file: {target}",
            )

        try:
            if kind == "directory":
                shutil.rmtree(target)
            else:
                # ``Path.unlink`` removes the link itself, including a broken
                # symlink, and never follows its target.
                target.unlink()
        except (PermissionError, FileNotFoundError, OSError) as exc:
            raise _wrapped_error("remove_target_no_follow", target, exc, kind=kind) from exc


FilesystemGateway = FilesystemCliGateway
FilesystemAdapter = FilesystemCliGateway


def _validated_path(path: Path) -> Path:
    if not isinstance(path, Path):
        raise FilesystemAdapterError(
            operation="validate_path",
            path=Path("."),
            message="path must be a pathlib.Path",
        )
    if "\x00" in str(path):
        raise FilesystemAdapterError(
            operation="validate_path",
            path=path,
            message="path contains a NUL byte",
        )
    return path


def _kind_from_mode(mode: int) -> str:
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISREG(mode):
        return "file"
    # Keep the established filesystem-kind vocabulary used by the extracted
    # runtime helpers: any non-directory/non-regular/non-symlink entry is
    # represented as ``other`` and is not a supported cleanup target.
    return "other"


def _wrapped_error(operation: str, path: Path, exc: OSError, *, kind: str | None = None) -> FilesystemAdapterError:
    return FilesystemAdapterError(
        operation=operation,
        path=path,
        kind=kind,
        message=f"{operation} failed for {path}: {exc}",
    )


__all__ = [
    "FilesystemAdapter",
    "FilesystemAdapterError",
    "FilesystemCliGateway",
    "FilesystemError",
    "FilesystemGateway",
    "FilesystemGatewayError",
]

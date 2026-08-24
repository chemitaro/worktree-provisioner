"""No-follow filesystem adapter for worktree artifacts."""

from __future__ import annotations

import contextlib
import os
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

    def open_directory(self, path: Path) -> DirectoryHandle:
        """Open a directory with every path component bound no-follow.

        The returned descriptor remains attached to the directory even if an
        ancestor is subsequently renamed.  Mutation callers use this handle
        for the final Git/cleanup operation instead of resolving the path a
        second time through a potentially replaced symlink.
        """

        target = _validated_path(path)
        try:
            fd = _open_directory_path(target)
        except (PermissionError, OSError) as exc:
            raise _wrapped_error("open_directory", target, exc) from exc
        return DirectoryHandle(fd=fd, path=target.absolute())

    def remove_target_no_follow_bound(self, directory: DirectoryHandle, target: Path) -> None:
        """Remove ``target`` beneath an already-open directory descriptor."""

        target_path = _validated_path(target)
        try:
            relative = target_path.absolute().relative_to(directory.path.absolute())
        except ValueError as exc:
            raise FilesystemAdapterError(
                operation="remove_target_no_follow_bound",
                path=target_path,
                kind="outside",
                message=f"cleanup target is outside the opened directory: {target_path}",
            ) from exc
        components = relative.parts
        if not components or any(component in {"", ".", ".."} for component in components):
            raise FilesystemAdapterError(
                operation="remove_target_no_follow_bound",
                path=target_path,
                kind="invalid",
                message=f"cleanup target is not a descendant: {target_path}",
            )

        parent_fd = os.dup(directory.fd)
        try:
            for component in components[:-1]:
                next_fd = _open_child_directory(parent_fd, component)
                os.close(parent_fd)
                parent_fd = next_fd
            _remove_entry_at(parent_fd, components[-1], target_path)
        except (PermissionError, FileNotFoundError, OSError) as exc:
            if isinstance(exc, FilesystemAdapterError):
                raise
            raise _wrapped_error("remove_target_no_follow_bound", target_path, exc) from exc
        finally:
            with contextlib.suppress(OSError):
                os.close(parent_fd)


@dataclass(slots=True)
class DirectoryHandle:
    """An owned, no-follow directory descriptor used as a mutation capability."""

    fd: int
    path: Path

    def __enter__(self) -> DirectoryHandle:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if self.fd >= 0:
            fd = self.fd
            self.fd = -1
            os.close(fd)

    def path_identity_matches(self) -> bool:
        """Return whether the lexical path still names this opened directory."""

        if self.fd < 0:
            return False
        try:
            current = os.stat(self.path, follow_symlinks=False)
            opened = os.fstat(self.fd)
        except OSError:
            return False
        return (current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino)


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


def _directory_flags() -> int:
    directory_flag = getattr(os, "O_DIRECTORY", None)
    nofollow_flag = getattr(os, "O_NOFOLLOW", None)
    if directory_flag is None or nofollow_flag is None:
        raise OSError("descriptor-bound directories are unavailable on this platform")
    return os.O_RDONLY | directory_flag | nofollow_flag | getattr(os, "O_CLOEXEC", 0)


def _open_directory_path(path: Path) -> int:
    target = path.absolute()
    if ".." in target.parts:
        raise OSError("directory path contains a parent traversal")
    parts = target.parts
    if not parts or not target.anchor:
        raise OSError("directory path must be absolute")
    fd = os.open(target.anchor, _directory_flags())
    try:
        for component in parts[1:]:
            next_fd = _open_child_directory(fd, component)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        with contextlib.suppress(OSError):
            os.close(fd)
        raise


def _open_child_directory(parent_fd: int, name: str) -> int:
    return os.open(name, _directory_flags(), dir_fd=parent_fd)


def _remove_entry_at(parent_fd: int, name: str, path: Path) -> None:
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise FilesystemAdapterError(
            operation="remove_target_no_follow_bound",
            path=path,
            kind="missing",
            message=f"cleanup target is missing: {path}",
        ) from exc
    kind = _kind_from_mode(info.st_mode)
    if kind == "other":
        raise FilesystemAdapterError(
            operation="remove_target_no_follow_bound",
            path=path,
            kind=kind,
            message=f"cleanup target is a special file: {path}",
        )
    if kind == "directory":
        child_fd = _open_child_directory(parent_fd, name)
        try:
            with os.scandir(child_fd) as entries:
                for entry in entries:
                    _remove_entry_at(child_fd, entry.name, path / entry.name)
        finally:
            os.close(child_fd)
        os.rmdir(name, dir_fd=parent_fd)
    else:
        # unlinkat-style removal by descriptor removes a symlink itself and
        # never follows its target.
        os.unlink(name, dir_fd=parent_fd)


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
    "DirectoryHandle",
    "FilesystemAdapter",
    "FilesystemAdapterError",
    "FilesystemCliGateway",
    "FilesystemError",
    "FilesystemGateway",
    "FilesystemGatewayError",
]

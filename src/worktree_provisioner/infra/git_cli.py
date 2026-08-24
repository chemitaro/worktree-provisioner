"""Git CLI adapter for the worktree-provisioner application ports.

This module deliberately keeps Git policy out of the adapter.  It executes
the small, fixed set of Git commands required by :class:`GitGateway`, turns
their output into application value objects, and exposes command failures as
typed adapter errors.  Candidate retry, target policy, and cleanup decisions
belong to the application layer.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, overload

from worktree_provisioner.application.contracts import CollisionKind, GitWorktreeRecord

_GIT_COMMAND: Final[str] = "git"
_DIAGNOSTIC_LIMIT: Final[int] = 4096
_HEAD = "HEAD"
_BRANCH_REF_PREFIX = "refs/heads/"


class GitAdapterError(RuntimeError):
    """Typed failure raised by the Git CLI adapter.

    ``argv`` is retained as a tuple for diagnostics and tests, while
    ``diagnostic`` is already bounded so command output cannot become an
    unbounded error payload.  ``returncode`` is ``None`` when Git could not be
    located or the process could not be started.  ``collision_kind`` is set
    only by ``add_worktree`` after a strict, operation-specific classification;
    callers must not infer retryability from ``diagnostic`` text.
    """

    __slots__ = ("argv", "collision_kind", "diagnostic", "operation", "returncode")

    operation: str
    argv: tuple[str, ...]
    returncode: int | None
    diagnostic: str
    collision_kind: CollisionKind | None

    def __init__(
        self,
        *,
        operation: str,
        argv: Sequence[str],
        message: str,
        returncode: int | None = None,
        diagnostic: str = "",
        collision_kind: CollisionKind | None = None,
    ) -> None:
        self.operation = operation
        self.argv = tuple(argv)
        self.returncode = returncode
        self.diagnostic = _bounded(diagnostic)
        self.collision_kind = collision_kind
        detail = self.diagnostic
        text = f"{message}: {detail}" if detail else message
        super().__init__(text)


# These aliases make the boundary explicit to callers that distinguish a
# command failure from the concrete adapter implementation.  They remain the
# same exception type and do not create a second error contract.
GitCommandError = GitAdapterError
InternalGitAdapterError = GitAdapterError


@dataclass(frozen=True, slots=True)
class GitCliGateway:
    """Concrete :class:`~worktree_provisioner.application.ports.GitGateway`.

    ``git_executable`` is injectable for isolated tests, but defaults to the
    process ``PATH`` lookup.  No shell is ever used for command execution.
    """

    git_executable: str = _GIT_COMMAND

    def resolve_checkout_root(self, path: Path) -> Path:
        """Resolve a checkout path using Git's own worktree discovery."""

        candidate = _validated_path(path, name="checkout path")
        completed = self._run(
            candidate,
            ("rev-parse", "--show-toplevel"),
            operation="resolve_checkout_root",
            text=False,
        )
        raw_root = _remove_stdout_delimiter(completed.stdout)
        if not raw_root:
            raise GitAdapterError(
                operation="resolve_checkout_root",
                argv=self._argv(("rev-parse", "--show-toplevel")),
                message="git returned an empty checkout root",
                returncode=completed.returncode,
                diagnostic=_diagnostic(_diagnostic_text(completed.stdout), _diagnostic_text(completed.stderr)),
            )
        root = Path(os.fsdecode(raw_root))
        try:
            candidate_identity = candidate.resolve(strict=True)
            root_identity = root.resolve(strict=True)
        except OSError as exc:
            raise GitAdapterError(
                operation="resolve_checkout_root",
                argv=self._argv(("rev-parse", "--show-toplevel")),
                message="failed to verify the Git checkout root",
                returncode=completed.returncode,
                diagnostic=str(exc),
            ) from exc
        try:
            candidate_identity.relative_to(root_identity)
        except ValueError:
            raise GitAdapterError(
                operation="resolve_checkout_root",
                argv=self._argv(("rev-parse", "--show-toplevel")),
                message="Git returned a checkout root outside the requested checkout",
                returncode=completed.returncode,
                diagnostic=f"requested={candidate_identity} returned={root_identity}",
            ) from None
        return root

    def current_branch_or_none(self, repo_root: Path) -> str | None:
        """Return the named branch, or ``None`` for detached ``HEAD``."""

        repo = _validated_path(repo_root, name="repository root")
        completed = self._run(
            repo,
            ("rev-parse", "--abbrev-ref", "HEAD"),
            operation="current_branch_or_none",
        )
        branch = _first_line(completed.stdout)
        if branch is None or branch == _HEAD:
            return None
        return branch

    def local_branch_exists(self, repo_root: Path, branch: str) -> bool:
        """Check a local branch with Git's quiet exact-ref command."""

        repo = _validated_path(repo_root, name="repository root")
        branch_name = _validated_branch(branch)
        completed = self._run(
            repo,
            ("show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"),
            operation="local_branch_exists",
            check=False,
        )
        return _boolean_command_result(
            completed,
            operation="local_branch_exists",
            argv=self._argv(("show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}")),
        )

    def check_branch_ref(self, repo_root: Path, branch: str) -> bool:
        """Validate a branch name through ``git check-ref-format --branch``."""

        repo = _validated_path(repo_root, name="repository root")
        branch_name = _validated_branch(branch)
        completed = self._run(
            repo,
            ("check-ref-format", "--branch", branch_name),
            operation="check_branch_ref",
            check=False,
        )
        return _boolean_command_result(
            completed,
            operation="check_branch_ref",
            argv=self._argv(("check-ref-format", "--branch", branch_name)),
        )

    def worktree_list(self, repo_root: Path) -> list[GitWorktreeRecord]:
        """Return Git's NUL-delimited porcelain worktree inventory."""

        repo = _validated_path(repo_root, name="repository root")
        completed = self._run(
            repo,
            ("worktree", "list", "--porcelain", "-z"),
            operation="worktree_list",
            text=False,
        )
        return parse_worktree_porcelain(completed.stdout)

    def add_worktree(self, repo_root: Path, *, path: Path, branch: str) -> None:
        """Add one worktree and branch using the exact fixed argv shape."""

        repo = _validated_path(repo_root, name="repository root")
        target = _validated_path(path, name="worktree path", require_exists=False)
        branch_name = _validated_branch(branch)
        completed = self._run(
            repo,
            ("worktree", "add", "-b", branch_name, str(target)),
            operation="add_worktree",
            check=False,
        )
        if completed.returncode == 0:
            return
        diagnostic = _diagnostic(completed.stdout, completed.stderr)
        raise GitAdapterError(
            operation="add_worktree",
            argv=self._argv(("worktree", "add", "-b", branch_name, str(target))),
            message=f"git command failed with exit code {completed.returncode}",
            returncode=completed.returncode,
            diagnostic=diagnostic,
            collision_kind=_classify_add_collision(diagnostic, branch=branch_name, path=target),
        )

    def remove_worktree(self, repo_root: Path, *, path: Path, force: bool) -> None:
        """Remove one worktree, adding exactly one ``--force`` when requested."""

        repo = _validated_path(repo_root, name="repository root")
        target = _validated_path(path, name="worktree path", require_exists=False)
        args = ("worktree", "remove", "--force", str(target)) if force else ("worktree", "remove", str(target))
        self._run(repo, args, operation="remove_worktree")

    def _argv(self, args: Sequence[str]) -> tuple[str, ...]:
        return (self.git_executable, *args)

    @overload
    def _run(
        self,
        repo_root: Path,
        args: Sequence[str],
        *,
        operation: str,
        check: bool = True,
        text: Literal[True] = True,
    ) -> subprocess.CompletedProcess[str]: ...

    @overload
    def _run(
        self,
        repo_root: Path,
        args: Sequence[str],
        *,
        operation: str,
        check: bool = True,
        text: Literal[False],
    ) -> subprocess.CompletedProcess[bytes]: ...

    def _run(
        self,
        repo_root: Path,
        args: Sequence[str],
        *,
        operation: str,
        check: bool = True,
        text: bool = True,
    ) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
        argv = self._argv(args)
        if shutil.which(self.git_executable) is None:
            raise GitAdapterError(
                operation=operation,
                argv=argv,
                message="git executable was not found",
            )

        try:
            completed = subprocess.run(
                list(argv),
                cwd=repo_root,
                capture_output=True,
                text=text,
                check=False,
                shell=False,
                env=_git_environment(),
            )
        except OSError as exc:
            raise GitAdapterError(
                operation=operation,
                argv=argv,
                message="failed to start git command",
                diagnostic=str(exc),
            ) from exc

        if check and completed.returncode != 0:
            raise GitAdapterError(
                operation=operation,
                argv=argv,
                message=f"git command failed with exit code {completed.returncode}",
                returncode=completed.returncode,
                diagnostic=_diagnostic(_diagnostic_text(completed.stdout), _diagnostic_text(completed.stderr)),
            )
        return completed


def parse_worktree_porcelain(output: str | bytes) -> list[GitWorktreeRecord]:
    """Parse text or NUL-delimited Git worktree porcelain output.

    The adapter uses Git's ``--porcelain -z`` form so path bytes are never
    confused with line or record delimiters.  The text form remains accepted
    for callers and historical fixtures that use the older newline protocol.
    """

    if isinstance(output, bytes):
        return _parse_worktree_porcelain_z(output)
    return _parse_worktree_porcelain_text(output)


def _parse_worktree_porcelain_text(text: str) -> list[GitWorktreeRecord]:
    """Parse the legacy line-delimited form without fabricating records."""

    records: list[GitWorktreeRecord] = []
    block: dict[str, object] = {}

    def flush() -> None:
        path = block.get("path")
        if not isinstance(path, str) or not path:
            return
        branch = block.get("branch")
        normalized_branch = branch if isinstance(branch, str) and branch else None
        if normalized_branch is not None and normalized_branch.startswith(_BRANCH_REF_PREFIX):
            normalized_branch = normalized_branch[len(_BRANCH_REF_PREFIX) :]
            if not normalized_branch:
                normalized_branch = None
        records.append(
            GitWorktreeRecord(
                path=Path(path),
                head=_optional_text(block.get("head")),
                branch=normalized_branch,
                detached=bool(block.get("detached", False)),
                bare=bool(block.get("bare", False)),
                locked=bool(block.get("locked", False)),
                lock_reason=_optional_text(block.get("lock_reason")),
            )
        )

    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if not line.strip():
            flush()
            block = {}
            continue
        if line.startswith("worktree "):
            flush()
            block = {"path": line[len("worktree ") :]}
        elif line.startswith("HEAD "):
            block["head"] = line[len("HEAD ") :].strip() or None
        elif line.startswith("branch "):
            block["branch"] = line[len("branch ") :].strip() or None
        elif line == "detached":
            block["detached"] = True
        elif line == "bare":
            block["bare"] = True
        elif line == "locked" or line.startswith("locked "):
            block["locked"] = True
            reason = line[len("locked") :].strip()
            block["lock_reason"] = reason or None
        # ``prunable`` and future Git metadata are deliberately ignored.

    flush()
    return records


def _parse_worktree_porcelain_z(data: bytes) -> list[GitWorktreeRecord]:
    """Parse Git's NUL-delimited porcelain fields without trimming values."""

    records: list[GitWorktreeRecord] = []
    block: dict[str, bytes] = {}

    def decode(value: bytes) -> str:
        return os.fsdecode(value)

    def flush() -> None:
        raw_path = block.get("path")
        if raw_path is None or not raw_path:
            return
        branch = block.get("branch")
        normalized_branch = decode(branch) if branch else None
        if normalized_branch is not None and normalized_branch.startswith(_BRANCH_REF_PREFIX):
            normalized_branch = normalized_branch[len(_BRANCH_REF_PREFIX) :] or None
        lock_reason = block.get("lock_reason")
        records.append(
            GitWorktreeRecord(
                path=Path(decode(raw_path)),
                head=decode(block["head"]) if block.get("head") else None,
                branch=normalized_branch,
                detached="detached" in block,
                bare="bare" in block,
                locked="locked" in block,
                lock_reason=decode(lock_reason) if lock_reason else None,
            )
        )

    for field in data.split(b"\0"):
        if field == b"":
            flush()
            block = {}
            continue
        if field.startswith(b"worktree "):
            flush()
            block = {"path": field[len(b"worktree ") :]}
        elif field.startswith(b"HEAD "):
            block["head"] = field[len(b"HEAD ") :]
        elif field.startswith(b"branch "):
            block["branch"] = field[len(b"branch ") :]
        elif field == b"detached":
            block["detached"] = field
        elif field == b"bare":
            block["bare"] = field
        elif field == b"locked" or field.startswith(b"locked "):
            block["locked"] = field
            block["lock_reason"] = field[len(b"locked") + 1 :] if field.startswith(b"locked ") else b""
        # ``prunable`` and future Git metadata are deliberately ignored.

    flush()
    return records


def _validated_path(path: Path, *, name: str, require_exists: bool = True) -> Path:
    """Validate a subprocess path without changing its spelling."""

    if not isinstance(path, Path):
        raise GitAdapterError(
            operation="validate_path",
            argv=(),
            message=f"{name} must be a pathlib.Path",
        )
    if "\x00" in str(path):
        raise GitAdapterError(
            operation="validate_path",
            argv=(),
            message=f"{name} contains a NUL byte",
        )
    if require_exists and (not path.exists() or not path.is_dir()):
        raise GitAdapterError(
            operation="validate_path",
            argv=(),
            message=f"{name} must be an existing directory: {path}",
        )
    return path


def _validated_branch(branch: str) -> str:
    if not isinstance(branch, str) or not branch or "\x00" in branch:
        raise GitAdapterError(
            operation="validate_branch",
            argv=(),
            message="branch must be a non-empty string without NUL bytes",
        )
    return branch


def _diagnostic_text(value: str | bytes | None) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) else os.fsdecode(value)


def _remove_stdout_delimiter(value: str | bytes | None) -> str | bytes | None:
    """Remove exactly Git's final newline delimiter, never path whitespace."""

    if not value:
        return None
    trimmed: str | bytes | None
    if isinstance(value, str):
        trimmed = value[:-1] if value.endswith("\n") else None
    else:
        trimmed = value[:-1] if value.endswith(b"\n") else None
    return trimmed if trimmed else None


def _first_line(value: str | None) -> str | None:
    if not value:
        return None
    line = value.splitlines()[0].strip()
    return line or None


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _classify_add_collision(
    diagnostic: str,
    *,
    branch: str,
    path: Path,
) -> CollisionKind | None:
    """Classify only Git's exact C-locale add-collision diagnostics.

    The adapter forces ``LC_ALL=C`` for Git subprocesses, then matches the
    complete operation-specific shape and the exact candidate branch/path.
    Generic fragments such as ``already exists`` are intentionally ignored;
    they can describe ref-lock, permission, or I/O failures.
    """

    lines = []
    for raw_line in diagnostic.splitlines():
        line = raw_line.strip()
        if line.startswith("stderr:") or line.startswith("stdout:"):
            line = line.split(":", 1)[1].strip()
        if line.startswith("fatal:"):
            line = line[len("fatal:") :].strip()
        lines.append(line)

    branch_collision = f"a branch named '{branch}' already exists"
    path_collision = f"'{path}' already exists"
    checked_out_collisions = (
        f"'{branch}' is already used by worktree at ",
        f"'{branch}' is already checked out at ",
    )
    for line in lines:
        if line == branch_collision:
            return "branch"
        if line == path_collision:
            return "path"
        if any(line.startswith(prefix) and line.endswith("'") for prefix in checked_out_collisions):
            return "checked_out"
    return None


def _git_environment() -> dict[str, str]:
    """Make Git's diagnostics deterministic without dropping user settings."""

    environment = dict(os.environ)
    environment["LC_ALL"] = "C"
    return environment


def _diagnostic(stdout: str | None, stderr: str | None) -> str:
    values = {
        "stderr": stderr.strip() if stderr and stderr.strip() else None,
        "stdout": stdout.strip() if stdout and stdout.strip() else None,
    }
    present = [name for name, value in values.items() if value is not None]
    if not present:
        return ""
    if len(present) == 1:
        name = present[0]
        value = values[name]
        assert value is not None
        return f"{name}: {_bounded(value, limit=_DIAGNOSTIC_LIMIT - len(name) - 2)}"

    # Keep both streams visible even when either one is very large.  This is
    # useful for Git diagnostics where stderr may only say "see stdout".
    label_budget = len("stderr: ") + len("stdout: ") + 1
    content_budget = max(0, _DIAGNOSTIC_LIMIT - label_budget)
    first_budget = content_budget // 2
    second_budget = content_budget - first_budget
    stderr_value = values["stderr"]
    stdout_value = values["stdout"]
    assert stderr_value is not None and stdout_value is not None
    return (
        f"stderr: {_bounded(stderr_value, limit=first_budget)}\nstdout: {_bounded(stdout_value, limit=second_budget)}"
    )


def _boolean_command_result(
    completed: subprocess.CompletedProcess[str], *, operation: str, argv: Sequence[str]
) -> bool:
    """Map only Git's documented negative result to ``False``.

    ``show-ref --verify --quiet`` and ``check-ref-format --branch`` use exit
    code 1 for an absent/invalid ref.  Other non-zero statuses indicate a
    command or repository failure and must remain visible to the application.
    """

    if completed.returncode == 0:
        return True
    if completed.returncode == 1:
        return False
    raise GitAdapterError(
        operation=operation,
        argv=argv,
        message=f"git command failed with exit code {completed.returncode}",
        returncode=completed.returncode,
        diagnostic=_diagnostic(completed.stdout, completed.stderr),
    )


def _bounded(value: str, *, limit: int = _DIAGNOSTIC_LIMIT) -> str:
    if len(value) <= limit:
        return value
    suffix = "...[truncated]"
    return value[: max(0, limit - len(suffix))] + suffix


__all__ = [
    "GitAdapterError",
    "GitCLIGateway",
    "GitCliGateway",
    "GitCommandError",
    "InternalGitAdapterError",
    "parse_worktree_porcelain",
]


# Accommodate the conventional initialism spelling without duplicating the
# implementation or creating a second runtime type.
GitCLIGateway = GitCliGateway

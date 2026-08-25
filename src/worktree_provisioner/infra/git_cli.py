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
from worktree_provisioner.encoding import filesystem_argument, restore_utf8_surrogates

_GIT_COMMAND: Final[str] = "git"
_DIAGNOSTIC_LIMIT: Final[int] = 4096
_HEAD = "HEAD"
_BRANCH_REF_PREFIX = "refs/heads/"
_GIT_AUTHORITY_ENVIRONMENT = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_INDEX_FILE",
        "GIT_GRAFT_FILE",
        "GIT_SHALLOW_FILE",
        "GIT_NAMESPACE",
        "GIT_PREFIX",
        "GIT_INTERNAL_SUPER_PREFIX",
        "GIT_IMPLICIT_WORK_TREE",
        "GIT_CEILING_DIRECTORIES",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_REPLACE_REF_BASE",
        "GIT_QUARANTINE_PATH",
        "GIT_CONFIG",
        "GIT_CONFIG_SYSTEM",
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_XDG",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_DISABLE",
        "GIT_CONFIG_ENVIRONMENT",
        "GIT_CONFIG_EXTENSIONS",
        "GIT_CONFIG_PARAMETERS",
        "GIT_CONFIG_COUNT",
    }
)


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
            ("worktree", "add", "-b", branch_name, "--", str(target)),
            operation="add_worktree",
            check=False,
        )
        if completed.returncode == 0:
            return
        diagnostic = _diagnostic(completed.stdout, completed.stderr)
        raise GitAdapterError(
            operation="add_worktree",
            argv=self._argv(("worktree", "add", "-b", branch_name, "--", str(target))),
            message=f"git command failed with exit code {completed.returncode}",
            returncode=completed.returncode,
            diagnostic=diagnostic,
            collision_kind=_classify_add_collision(diagnostic, branch=branch_name, path=target),
        )

    def add_worktree_bound(self, repo_root: Path, *, directory: object, name: str, branch: str) -> None:
        """Add a worktree relative to an already-open namespace directory.

        ``directory`` is the descriptor capability supplied by the no-follow
        filesystem adapter.  Git is explicitly pointed at the repository;
        the child process is then moved to the open directory descriptor
        before Git resolves the relative target.  This keeps a namespace
        rename/symlink replacement from redirecting the mutation.
        """

        repo = _validated_path(repo_root, name="repository root")
        fd = getattr(directory, "fd", None)
        directory_path = getattr(directory, "path", None)
        if not isinstance(fd, int) or fd < 0 or not isinstance(directory_path, Path):
            raise GitAdapterError(
                operation="add_worktree_bound",
                argv=(),
                message="worktree namespace capability is invalid",
            )
        within_root = getattr(directory, "is_within_bound_root", None)
        if callable(within_root) and not within_root():
            raise GitAdapterError(
                operation="add_worktree_bound",
                argv=(),
                message="worktree namespace is outside the managed root",
            )
        target_name = _validated_relative_name(name)
        branch_name = _validated_branch(branch)
        common_git_dir = self._common_git_dir(repo)
        start_point = self._head_commit(repo)
        if callable(within_root) and not within_root():
            raise GitAdapterError(
                operation="add_worktree_bound",
                argv=(),
                message="worktree namespace is outside the managed root",
            )
        args = (
            "--git-dir",
            str(common_git_dir),
            "worktree",
            "add",
            "-b",
            branch_name,
            "--",
            target_name,
            start_point,
        )
        completed = self._run(
            repo,
            args,
            operation="add_worktree_bound",
            check=False,
            cwd_fd=fd,
        )
        if completed.returncode == 0:
            identity_matches = getattr(directory, "path_identity_matches", None)
            if callable(identity_matches) and not identity_matches():
                raise GitAdapterError(
                    operation="add_worktree_bound",
                    argv=self._argv(args),
                    message="managed namespace changed during Git worktree add",
                    returncode=completed.returncode,
                )
            return
        diagnostic = _diagnostic(completed.stdout, completed.stderr)
        # Git reports the relative operand used by this fd-bound command,
        # rather than the descriptor's lexical path.
        target = Path(target_name)
        raise GitAdapterError(
            operation="add_worktree_bound",
            argv=self._argv(args),
            message=f"git command failed with exit code {completed.returncode}",
            returncode=completed.returncode,
            diagnostic=diagnostic,
            collision_kind=_classify_add_collision(diagnostic, branch=branch_name, path=target),
        )

    def _head_commit(self, repo_root: Path) -> str:
        """Return the invocation checkout HEAD for an explicit add start point."""

        completed = self._run(
            repo_root,
            ("rev-parse", "--verify", "HEAD"),
            operation="resolve_head",
            text=False,
        )
        raw = _remove_stdout_delimiter(completed.stdout)
        if not raw:
            raise GitAdapterError(
                operation="resolve_head",
                argv=self._argv(("rev-parse", "--verify", "HEAD")),
                message="git returned an empty HEAD",
                returncode=completed.returncode,
            )
        value = os.fsdecode(raw) if isinstance(raw, bytes) else raw
        if not value or any(character in value for character in "\r\n\x00"):
            raise GitAdapterError(
                operation="resolve_head",
                argv=self._argv(("rev-parse", "--verify", "HEAD")),
                message="git returned an invalid HEAD",
                returncode=completed.returncode,
            )
        return value

    def _common_git_dir(self, repo_root: Path) -> Path:
        """Resolve Git's shared administrative directory before fd binding."""

        completed = self._run(
            repo_root,
            ("rev-parse", "--git-common-dir"),
            operation="resolve_git_common_dir",
            text=False,
        )
        raw = _remove_stdout_delimiter(completed.stdout)
        if not raw:
            raise GitAdapterError(
                operation="resolve_git_common_dir",
                argv=self._argv(("rev-parse", "--git-common-dir")),
                message="git returned an empty common directory",
                returncode=completed.returncode,
            )
        common = Path(os.fsdecode(raw))
        return common if common.is_absolute() else (repo_root / common).absolute()

    def remove_worktree(self, repo_root: Path, *, path: Path, force: bool) -> None:
        """Remove one worktree, adding exactly one ``--force`` when requested."""

        repo = _validated_path(repo_root, name="repository root")
        target = _validated_path(path, name="worktree path", require_exists=False)
        args = (
            ("worktree", "remove", "--force", "--", str(target)) if force else ("worktree", "remove", "--", str(target))
        )
        self._run(repo, args, operation="remove_worktree")

    def remove_worktree_bound(self, repo_root: Path, *, directory: object, name: str, force: bool) -> None:
        """Remove a worktree using a root-bound namespace directory fd."""

        repo = _validated_path(repo_root, name="repository root")
        fd = getattr(directory, "fd", None)
        if not isinstance(fd, int) or fd < 0:
            raise GitAdapterError(
                operation="remove_worktree_bound",
                argv=(),
                message="worktree namespace capability is invalid",
            )
        within_root = getattr(directory, "is_within_bound_root", None)
        if callable(within_root) and not within_root():
            raise GitAdapterError(
                operation="remove_worktree_bound",
                argv=(),
                message="worktree namespace is outside the managed root",
            )
        target_name = _validated_relative_name(name)
        common_git_dir = self._common_git_dir(repo)
        if callable(within_root) and not within_root():
            raise GitAdapterError(
                operation="remove_worktree_bound",
                argv=(),
                message="worktree namespace is outside the managed root",
            )
        args = (
            "--git-dir",
            str(common_git_dir),
            "worktree",
            "remove",
            *(("--force",) if force else ()),
            "--",
            target_name,
        )
        self._run(repo, args, operation="remove_worktree_bound", cwd_fd=fd)

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
        cwd_fd: int | None = None,
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
        cwd_fd: int | None = None,
    ) -> subprocess.CompletedProcess[bytes]: ...

    def _run(
        self,
        repo_root: Path,
        args: Sequence[str],
        *,
        operation: str,
        check: bool = True,
        text: bool = True,
        cwd_fd: int | None = None,
    ) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
        argv = self._argv(args)
        if shutil.which(self.git_executable) is None:
            raise GitAdapterError(
                operation=operation,
                argv=argv,
                message="git executable was not found",
            )

        completed: subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]
        subprocess_argv = [filesystem_argument(argument) for argument in argv]
        try:
            if cwd_fd is None:
                if text:
                    completed = subprocess.run(
                        subprocess_argv,
                        cwd=repo_root,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="surrogateescape",
                        check=False,
                        shell=False,
                        env=_git_environment(),
                    )
                else:
                    completed = subprocess.run(
                        subprocess_argv,
                        cwd=repo_root,
                        capture_output=True,
                        text=False,
                        check=False,
                        shell=False,
                        env=_git_environment(),
                    )
            else:
                if text:
                    completed = subprocess.run(
                        subprocess_argv,
                        cwd=repo_root,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="surrogateescape",
                        check=False,
                        shell=False,
                        env=_git_environment(),
                        pass_fds=(cwd_fd,),
                        preexec_fn=lambda: os.fchdir(cwd_fd),
                    )
                else:
                    completed = subprocess.run(
                        subprocess_argv,
                        cwd=repo_root,
                        capture_output=True,
                        text=False,
                        check=False,
                        shell=False,
                        env=_git_environment(),
                        pass_fds=(cwd_fd,),
                        preexec_fn=lambda: os.fchdir(cwd_fd),
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
        normalized_branch = restore_utf8_surrogates(branch) if isinstance(branch, str) and branch else None
        if normalized_branch is not None and normalized_branch.startswith(_BRANCH_REF_PREFIX):
            normalized_branch = normalized_branch[len(_BRANCH_REF_PREFIX) :]
            if not normalized_branch:
                normalized_branch = None
        lock_reason = _optional_text(block.get("lock_reason"))
        records.append(
            GitWorktreeRecord(
                path=Path(path),
                head=_optional_text(block.get("head")),
                branch=normalized_branch,
                detached=bool(block.get("detached", False)),
                bare=bool(block.get("bare", False)),
                locked=bool(block.get("locked", False)),
                lock_reason=restore_utf8_surrogates(lock_reason) if lock_reason is not None else None,
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
        normalized_branch = restore_utf8_surrogates(decode(branch)) if branch else None
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
                lock_reason=restore_utf8_surrogates(decode(lock_reason)) if lock_reason else None,
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


def _validated_relative_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\x00" in name:
        raise GitAdapterError(
            operation="validate_path",
            argv=(),
            message="worktree name must be a non-empty string without NUL bytes",
        )
    relative = Path(name)
    if relative.is_absolute():
        raise GitAdapterError(
            operation="validate_path",
            argv=(),
            message="bound worktree name must be a relative path",
        )
    if any(component in {".", ".."} for component in name.split("/")):
        raise GitAdapterError(
            operation="validate_path",
            argv=(),
            message="bound worktree name must not contain '.' or '..' path components",
        )
    return name


def _diagnostic_text(value: str | bytes | None) -> str | None:
    if value is None:
        return None
    return restore_utf8_surrogates(value if isinstance(value, str) else os.fsdecode(value))


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
    line = restore_utf8_surrogates(value.splitlines()[0].strip())
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
    path_collision = f"'{restore_utf8_surrogates(str(path))}' already exists"
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
    """Make Git deterministic without inheriting repository authority."""

    environment = {key: value for key, value in os.environ.items() if not _is_git_authority_environment(key)}
    environment["LC_ALL"] = "C"
    return environment


def _is_git_authority_environment(key: str) -> bool:
    return key in _GIT_AUTHORITY_ENVIRONMENT or key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))


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

"""Git CLI adapter for the worktree-provisioner application ports.

This module deliberately keeps Git policy out of the adapter.  It executes
the small, fixed set of Git commands required by :class:`GitGateway`, turns
their output into application value objects, and exposes command failures as
typed adapter errors.  Candidate retry, target policy, and cleanup decisions
belong to the application layer.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from worktree_provisioner.application.contracts import GitWorktreeRecord

_GIT_COMMAND: Final[str] = "git"
_DIAGNOSTIC_LIMIT: Final[int] = 4096
_HEAD = "HEAD"
_BRANCH_REF_PREFIX = "refs/heads/"


class GitAdapterError(RuntimeError):
    """Typed failure raised by the Git CLI adapter.

    ``argv`` is retained as a tuple for diagnostics and tests, while
    ``diagnostic`` is already bounded so command output cannot become an
    unbounded error payload.  ``returncode`` is ``None`` when Git could not be
    located or the process could not be started.
    """

    __slots__ = ("argv", "diagnostic", "operation", "returncode")

    operation: str
    argv: tuple[str, ...]
    returncode: int | None
    diagnostic: str

    def __init__(
        self,
        *,
        operation: str,
        argv: Sequence[str],
        message: str,
        returncode: int | None = None,
        diagnostic: str = "",
    ) -> None:
        self.operation = operation
        self.argv = tuple(argv)
        self.returncode = returncode
        self.diagnostic = _bounded(diagnostic)
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
        )
        raw_root = _first_line(completed.stdout)
        if raw_root is None:
            raise GitAdapterError(
                operation="resolve_checkout_root",
                argv=self._argv(("rev-parse", "--show-toplevel")),
                message="git returned an empty checkout root",
                returncode=completed.returncode,
                diagnostic=_diagnostic(completed.stdout, completed.stderr),
            )
        return Path(raw_root)

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
        """Return Git's porcelain worktree inventory."""

        repo = _validated_path(repo_root, name="repository root")
        completed = self._run(
            repo,
            ("worktree", "list", "--porcelain"),
            operation="worktree_list",
        )
        return parse_worktree_porcelain(completed.stdout)

    def add_worktree(self, repo_root: Path, *, path: Path, branch: str) -> None:
        """Add one worktree and branch using the exact fixed argv shape."""

        repo = _validated_path(repo_root, name="repository root")
        target = _validated_path(path, name="worktree path", require_exists=False)
        branch_name = _validated_branch(branch)
        self._run(
            repo,
            ("worktree", "add", "-b", branch_name, str(target)),
            operation="add_worktree",
        )

    def remove_worktree(self, repo_root: Path, *, path: Path, force: bool) -> None:
        """Remove one worktree, adding exactly one ``--force`` when requested."""

        repo = _validated_path(repo_root, name="repository root")
        target = _validated_path(path, name="worktree path", require_exists=False)
        args = (
            ("worktree", "remove", "--force", str(target))
            if force
            else ("worktree", "remove", str(target))
        )
        self._run(repo, args, operation="remove_worktree")

    def _argv(self, args: Sequence[str]) -> tuple[str, ...]:
        return (self.git_executable, *args)

    def _run(
        self,
        repo_root: Path,
        args: Sequence[str],
        *,
        operation: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
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
                text=True,
                check=False,
                shell=False,
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
                diagnostic=_diagnostic(completed.stdout, completed.stderr),
            )
        return completed


def parse_worktree_porcelain(text: str) -> list[GitWorktreeRecord]:
    """Parse ``git worktree list --porcelain`` without relying on a final blank.

    Git emits one block per worktree.  Unknown metadata lines are ignored so
    newer Git fields do not break inventory parsing; blocks without a valid
    ``worktree`` line are not returned.  This also makes an empty or malformed
    response safely produce an empty inventory instead of a fabricated record.
    """

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


def _first_line(value: str | None) -> str | None:
    if not value:
        return None
    line = value.splitlines()[0].strip()
    return line or None


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


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
        f"stderr: {_bounded(stderr_value, limit=first_budget)}\n"
        f"stdout: {_bounded(stdout_value, limit=second_budget)}"
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

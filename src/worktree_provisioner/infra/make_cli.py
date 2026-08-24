"""``make init`` bootstrap adapter.

The adapter only reports observable bootstrap state.  Whether bootstrap is
requested, how a failure affects a create operation, and whether the
repository is trusted are application/CLI policy decisions.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol, cast

from worktree_provisioner.application.contracts import BootstrapResult

_MAKE_COMMAND: Final[str] = "make"
_DIAGNOSTIC_LIMIT: Final[int] = 4096
_MAKEFILE_NAMES: Final[tuple[str, ...]] = ("GNUmakefile", "makefile", "Makefile")
_MISSING_INIT_TARGET_FRAGMENTS: Final[tuple[str, ...]] = (
    "no rule to make target 'init'",
    "no rule to make target `init'",
    "no rule to make target init",
    "no targets specified and no makefile found",
    "no makefile found",
)


class MakeRunner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        capture_output: bool,
        text: bool,
        check: bool,
        shell: bool,
    ) -> subprocess.CompletedProcess[str]: ...


class MakeAdapterError(RuntimeError):
    """Unexpected failure while probing or invoking ``make``."""

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
        suffix = f": {detail}" if detail else ""
        super().__init__(f"{message}{suffix}")


# Descriptive aliases make the typed adapter boundary discoverable without
# creating separate exception contracts.
MakeCommandError = MakeAdapterError
BootstrapAdapterError = MakeAdapterError


@dataclass(frozen=True, slots=True)
class MakeCliGateway:
    """Run the fixed detection/execution sequence for ``make init``.

    ``runner`` is injectable for unit tests.  The default is resolved at call
    time so monkeypatching :mod:`subprocess` remains effective.
    """

    make_executable: str = _MAKE_COMMAND
    runner: MakeRunner | None = None

    def run_make_init_if_available(self, worktree_path: Path) -> BootstrapResult:
        """Return the bootstrap state for one already-created worktree."""

        target = _validated_worktree_path(worktree_path)
        detection_argv = (self.make_executable, "-n", "init")
        try:
            has_makefile = _has_standard_makefile(target)
        except OSError as exc:
            return BootstrapResult(
                requested=True,
                status="detection_failed",
                command=detection_argv,
                exit_code=None,
                detail=_bounded(str(exc)),
            )
        if not has_makefile:
            return _skipped()

        if shutil.which(self.make_executable) is None:
            return BootstrapResult(
                requested=True,
                status="detection_failed",
                command=detection_argv,
                exit_code=None,
                detail=f"{self.make_executable} executable was not found",
            )

        try:
            detected = self._run(detection_argv, target)
        except OSError as exc:
            return BootstrapResult(
                requested=True,
                status="detection_failed",
                command=detection_argv,
                exit_code=None,
                detail=_bounded(str(exc)),
            )

        detection_detail = _diagnostic(detected.stdout, detected.stderr)
        if detected.returncode != 0:
            if _is_missing_init_target(detection_detail):
                return _skipped()
            return BootstrapResult(
                requested=True,
                status="detection_failed",
                command=detection_argv,
                exit_code=detected.returncode,
                detail=detection_detail or "make init detection failed",
            )

        execution_argv = (self.make_executable, "init")
        try:
            executed = self._run(execution_argv, target)
        except OSError as exc:
            return BootstrapResult(
                requested=True,
                status="failed",
                command=execution_argv,
                # ``BootstrapResult`` requires a failed execution to expose a
                # non-zero exit status.  A process that cannot be started is
                # represented as the conventional non-zero failure code.
                exit_code=1,
                detail=_bounded(str(exc)),
            )

        execution_detail = _diagnostic(executed.stdout, executed.stderr)
        if executed.returncode == 0:
            return BootstrapResult(
                requested=True,
                status="succeeded",
                command=execution_argv,
                exit_code=0,
                detail=None,
            )
        return BootstrapResult(
            requested=True,
            status="failed",
            command=execution_argv,
            exit_code=executed.returncode,
            detail=execution_detail or "make init failed",
        )

    def _run(self, argv: tuple[str, ...], cwd: Path) -> subprocess.CompletedProcess[str]:
        runner = self.runner or cast(MakeRunner, subprocess.run)
        return runner(
            list(argv),
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )


MakeGateway = MakeCliGateway
BootstrapCliGateway = MakeCliGateway


def _validated_worktree_path(path: Path) -> Path:
    if not isinstance(path, Path):
        raise MakeAdapterError(
            operation="validate_worktree_path",
            argv=(),
            message="worktree path must be a pathlib.Path",
        )
    if "\x00" in str(path):
        raise MakeAdapterError(
            operation="validate_worktree_path",
            argv=(),
            message="worktree path contains a NUL byte",
        )
    try:
        valid_directory = path.exists() and path.is_dir()
    except OSError as exc:
        raise MakeAdapterError(
            operation="validate_worktree_path",
            argv=(),
            message=f"failed to inspect worktree path: {path}",
            diagnostic=str(exc),
        ) from exc
    if not valid_directory:
        raise MakeAdapterError(
            operation="validate_worktree_path",
            argv=(),
            message=f"worktree path must be an existing directory: {path}",
        )
    return path


def _has_standard_makefile(worktree_path: Path) -> bool:
    # ``is_file`` follows a makefile symlink; the namespace no-follow policy
    # belongs to the filesystem/application boundary, not this adapter.
    return any((worktree_path / name).is_file() for name in _MAKEFILE_NAMES)


def _skipped() -> BootstrapResult:
    return BootstrapResult(
        requested=True,
        status="skipped",
        command=None,
        exit_code=None,
        detail=None,
    )


def _is_missing_init_target(detail: str) -> bool:
    lowered = detail.lower()
    return any(fragment in lowered for fragment in _MISSING_INIT_TARGET_FRAGMENTS)


def _diagnostic(stdout: str | None, stderr: str | None) -> str:
    streams: list[tuple[str, str]] = []
    if stderr and stderr.strip():
        streams.append(("stderr", stderr.strip()))
    if stdout and stdout.strip():
        streams.append(("stdout", stdout.strip()))
    if not streams:
        return ""
    if len(streams) == 1:
        label, value = streams[0]
        return f"{label}: {_bounded(value, limit=_DIAGNOSTIC_LIMIT - len(label) - 2)}"

    prefix_budget = len("stderr: ") + len("stdout: ") + 1
    content_budget = max(0, _DIAGNOSTIC_LIMIT - prefix_budget)
    first_budget = content_budget // 2
    second_budget = content_budget - first_budget
    return (
        f"stderr: {_bounded(streams[0][1], limit=first_budget)}\nstdout: {_bounded(streams[1][1], limit=second_budget)}"
    )


def _bounded(value: str, *, limit: int = _DIAGNOSTIC_LIMIT) -> str:
    if len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return f"{value[: limit - 1]}…"


__all__ = [
    "BootstrapAdapterError",
    "BootstrapCliGateway",
    "MakeAdapterError",
    "MakeCliGateway",
    "MakeCommandError",
    "MakeGateway",
]

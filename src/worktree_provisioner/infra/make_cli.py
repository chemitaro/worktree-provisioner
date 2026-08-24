"""``make init`` bootstrap adapter.

The adapter only reports observable bootstrap state.  Whether bootstrap is
requested, how a failure affects a create operation, and whether the
repository is trusted are application/CLI policy decisions.
"""

from __future__ import annotations

import os
import re
import secrets
import selectors
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final, Protocol, cast

from worktree_provisioner.application.contracts import BootstrapResult

_MAKE_COMMAND: Final[str] = "make"
_DIAGNOSTIC_LIMIT: Final[int] = 4096
_STREAM_CHUNK_SIZE: Final[int] = 8192
_MAKEFILE_NAMES: Final[tuple[str, ...]] = ("GNUmakefile", "makefile", "Makefile")
_MATCHER_LINE_LIMIT: Final[int] = 8192


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
        self.diagnostic = _safe_bounded(diagnostic)
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
        # Keep the public detection command stable.  The concrete probe uses
        # a temporary overlay and controlled markers rather than classifying
        # arbitrary diagnostic text from the repository.
        detection_argv = (self.make_executable, "-n", "init")
        try:
            makefile = _find_standard_makefile(target)
        except OSError as exc:
            return BootstrapResult(
                requested=True,
                status="detection_failed",
                command=detection_argv,
                exit_code=None,
                detail=_safe_bounded(str(exc)),
            )
        if makefile is None:
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
            detected, has_init_target = self._run_detection(detection_argv, target, makefile)
        except OSError as exc:
            return BootstrapResult(
                requested=True,
                status="detection_failed",
                command=detection_argv,
                exit_code=None,
                detail=_safe_bounded(str(exc)),
            )

        detection_detail = _diagnostic(detected.stdout, detected.stderr)
        if detected.returncode != 0:
            return BootstrapResult(
                requested=True,
                status="detection_failed",
                command=detection_argv,
                exit_code=detected.returncode,
                detail=detection_detail or "make init detection failed",
            )
        if not has_init_target:
            return _skipped()

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
                detail=_safe_bounded(str(exc)),
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
        if self.runner is not None:
            # The injectable runner is a unit-test seam.  Normalize its result
            # as well so fake adapters cannot bypass the public diagnostic
            # redaction/boundary.
            completed = self.runner(
                list(argv),
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
            )
            return _bounded_completed_process(completed)
        return _run_bounded_process(argv, cwd)

    def _run_detection(
        self, argv: tuple[str, ...], cwd: Path, makefile: Path
    ) -> tuple[subprocess.CompletedProcess[str], bool]:
        """Run target detection with a structural, repository-independent marker."""

        if self.runner is not None:
            # Inspect the injected result before applying the public
            # diagnostic bound so a long synthetic prelude cannot hide a
            # structurally present target in tests.
            raw = self.runner(
                list(argv),
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                shell=False,
            )
            has_init_target = _contains_make_target(raw.stdout if isinstance(raw.stdout, str) else "", "init")
            return _bounded_completed_process(raw), has_init_target
        return _run_structural_probe(self.make_executable, cwd, makefile)


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


def _find_standard_makefile(worktree_path: Path) -> Path | None:
    for name in _MAKEFILE_NAMES:
        candidate = worktree_path / name
        if candidate.is_file():
            return candidate
    return None


def _skipped() -> BootstrapResult:
    return BootstrapResult(
        requested=True,
        status="skipped",
        command=None,
        exit_code=None,
        detail=None,
    )


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
        return f"{label}: {_safe_bounded(value, limit=_DIAGNOSTIC_LIMIT - len(label) - 2)}"

    prefix_budget = len("stderr: ") + len("stdout: ") + 1
    content_budget = max(0, _DIAGNOSTIC_LIMIT - prefix_budget)
    first_budget = content_budget // 2
    second_budget = content_budget - first_budget
    return (
        f"stderr: {_safe_bounded(streams[0][1], limit=first_budget)}\n"
        f"stdout: {_safe_bounded(streams[1][1], limit=second_budget)}"
    )


def _bounded(value: str, *, limit: int = _DIAGNOSTIC_LIMIT) -> str:
    if len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return f"{value[: limit - 1]}…"


_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?P<prefix>[A-Z0-9_.-]*(?:TOKEN|PASSWORD|SECRET|AUTHORIZATION|API[_-]?KEY)[A-Z0-9_.-]*\s*[:=]\s*)(?P<value>[^\s,;]+)"
)
_AUTHORIZATION_HEADER = re.compile(r"(?im)(?P<prefix>\bAuthorization\b\s*:\s*)(?:Bearer\s+)?[^\r\n]+")
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[^\s,;]+")


def _redact_secrets(value: str) -> str:
    """Remove representative credential values before diagnostics escape."""

    redacted = _SECRET_ASSIGNMENT.sub(r"\g<prefix>[REDACTED]", value)
    redacted = _AUTHORIZATION_HEADER.sub(r"\g<prefix>[REDACTED]", redacted)
    return _BEARER_TOKEN.sub("Bearer [REDACTED]", redacted)


def _safe_bounded(value: str, *, limit: int = _DIAGNOSTIC_LIMIT) -> str:
    """Redact first, then apply the externally visible hard character bound."""

    return _bounded(_redact_secrets(value), limit=limit)


def _bounded_completed_process(completed: subprocess.CompletedProcess[str]) -> subprocess.CompletedProcess[str]:
    """Normalize an injected runner result to the same safe boundary."""

    stdout = completed.stdout if isinstance(completed.stdout, str) else ""
    stderr = completed.stderr if isinstance(completed.stderr, str) else ""
    return subprocess.CompletedProcess(
        completed.args,
        completed.returncode,
        stdout=_safe_bounded(stdout),
        stderr=_safe_bounded(stderr),
    )


def _run_structural_probe(
    make_executable: str, cwd: Path, makefile: Path
) -> tuple[subprocess.CompletedProcess[str], bool]:
    """Probe ``init`` through a controlled prerequisite, not human output.

    A temporary overlay adds a unique probe target whose prerequisite is
    ``init``.  The repository makefile and overlay are passed as separate
    ``-f`` arguments, so paths are never re-parsed as make include syntax.
    A direct explicit-target probe is accepted when it succeeds, preserving
    repository-defined implicit and ``.DEFAULT`` rules; the overlay is used
    only to distinguish an absent ``init`` after that probe fails.
    ``.DEFAULT`` supplies a second unique marker only when that prerequisite
    is absent.  ``-n`` preserves the no-recipe-execution boundary, while the
    bounded process reader drains all output without retaining it.
    """

    repository_makefile = makefile.resolve(strict=False)
    direct = _run_bounded_process(
        (make_executable, "-n", "-f", str(repository_makefile), "init"),
        cwd,
    )
    if direct.returncode == 0:
        return direct, True

    token = secrets.token_hex(16)
    sentinel = f"__worktree_provisioner_probe_{token}"
    present_marker = f"__worktree_provisioner_present_{token}"
    missing_marker = f"__worktree_provisioner_missing_{token}"
    overlay = (
        ".DEFAULT:\n"
        f"\t@printf '%s\\n' '{missing_marker}:$@'\n"
        f".PHONY: {sentinel}\n"
        f"{sentinel}: init\n"
        f"\t@printf '%s\\n' '{present_marker}'\n"
    )
    states = {"present": False, "missing_init": False, "missing_other": False}

    def marker_matcher(line: str) -> bool:
        if present_marker in line:
            states["present"] = True
        if f"{missing_marker}:init" in line:
            states["missing_init"] = True
        elif missing_marker in line:
            states["missing_other"] = True
        return states["present"] or states["missing_init"] or states["missing_other"]

    with tempfile.TemporaryDirectory(prefix="worktree-provisioner-make-") as directory:
        overlay_path = Path(directory) / "Makefile"
        overlay_path.write_text(overlay, encoding="utf-8")
        completed, _ = _run_bounded_process_with_matcher(
            (make_executable, "-n", "-f", str(repository_makefile), "-f", str(overlay_path), sentinel),
            cwd,
            marker_matcher,
        )
    marker_observed = states["present"] or states["missing_init"] or states["missing_other"]
    if completed.returncode == 0 and (states["missing_other"] or not marker_observed):
        completed = subprocess.CompletedProcess(
            completed.args,
            1,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
    return completed, states["present"] and not states["missing_init"] and not states["missing_other"]


def _run_bounded_process(argv: tuple[str, ...], cwd: Path) -> subprocess.CompletedProcess[str]:
    completed, _ = _run_bounded_process_with_matcher(argv, cwd, None)
    return completed


def _run_bounded_process_with_matcher(
    argv: tuple[str, ...],
    cwd: Path,
    matcher: Callable[[str], bool] | None,
) -> tuple[subprocess.CompletedProcess[str], bool]:
    """Run a command while draining both pipes and retaining only a prefix.

    ``subprocess.run(capture_output=True)`` must not be used here: it retains
    an unbounded child output before this adapter can truncate it.  The
    selector loop continues draining after each per-stream limit so a noisy
    Makefile cannot deadlock the child, while only bounded bytes are stored.
    """

    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
    )
    assert process.stdout is not None and process.stderr is not None
    stdout_fd = process.stdout.fileno()
    stderr_fd = process.stderr.fileno()
    streams: dict[int, bytearray] = {stdout_fd: bytearray(), stderr_fd: bytearray()}
    matcher_buffer = ""
    matcher_found = False
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    selector.register(process.stderr, selectors.EVENT_READ)
    try:
        while selector.get_map():
            for key, _ in selector.select():
                stream = cast(BinaryIO, key.fileobj)
                chunk = os.read(stream.fileno(), _STREAM_CHUNK_SIZE)
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                if matcher is not None and stream.fileno() == stdout_fd and not matcher_found:
                    matcher_buffer += chunk.decode("utf-8", errors="replace")
                    while "\n" in matcher_buffer:
                        line, matcher_buffer = matcher_buffer.split("\n", 1)
                        if matcher(line.rstrip("\r")):
                            matcher_found = True
                            break
                    if not matcher_found and len(matcher_buffer) > _MATCHER_LINE_LIMIT:
                        # A malformed/no-newline line must not turn the
                        # structural probe into an unbounded accumulator.
                        matcher_buffer = matcher_buffer[-_MATCHER_LINE_LIMIT:]
                retained = streams[stream.fileno()]
                if len(retained) < _DIAGNOSTIC_LIMIT:
                    retained.extend(chunk[: _DIAGNOSTIC_LIMIT - len(retained)])
        returncode = process.wait()
    except BaseException:
        process.kill()
        process.wait()
        raise
    finally:
        selector.close()
        for pipe in (process.stdout, process.stderr):
            assert pipe is not None
            if not pipe.closed:
                pipe.close()

    if matcher is not None and not matcher_found and matcher_buffer and matcher(matcher_buffer.rstrip("\r")):
        matcher_found = True

    return subprocess.CompletedProcess(
        list(argv),
        returncode,
        stdout=bytes(streams[stdout_fd]).decode("utf-8", errors="replace"),
        stderr=bytes(streams[stderr_fd]).decode("utf-8", errors="replace"),
    ), matcher_found


def _is_make_target_line(line: str, target: str) -> bool:
    return line.startswith(f"{target}:")


def _contains_make_target(output: str, target: str) -> bool:
    return any(_is_make_target_line(line, target) for line in output.splitlines())


__all__ = [
    "BootstrapAdapterError",
    "BootstrapCliGateway",
    "MakeAdapterError",
    "MakeCliGateway",
    "MakeCommandError",
    "MakeGateway",
]

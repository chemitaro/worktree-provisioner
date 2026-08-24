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
    """Prove an absent ``init`` from make's own parsed rule database.

    The direct command remains the authority for success and failure.  Only
    after a direct failure do we ask make for its C-locale database while
    adding an independent phony goal.  The overlay never replaces
    ``.DEFAULT`` or any repository rule, and the repository makefile is passed
    by its original basename so relative includes and ``MAKEFILE_LIST`` keep
    their normal identity.  A complete database with neither an explicit
    ``init`` nor a repository ``.DEFAULT`` proves absence; all other failures
    stay detection failures.
    """

    direct = _run_bounded_process((make_executable, "-n", "init"), cwd)
    if direct.returncode == 0:
        return direct, True

    token = secrets.token_hex(16)
    probe_target = f"__worktree_provisioner_database_{token}"
    overlay = f".PHONY: {probe_target}\n{probe_target}: ;\n"

    def new_states() -> dict[str, bool]:
        return {
            "database_started": False,
            "files_section": False,
            "database_finished": False,
            "not_target": False,
            "probe_target_seen": False,
            "explicit_init": False,
            "explicit_default": False,
        }

    def database_matcher(states: dict[str, bool], expected_target: str) -> Callable[[str], bool]:
        def match(line: str) -> bool:
            if line.startswith("# Make data base, printed on "):
                states["database_started"] = True
                return False
            if not states["database_started"]:
                return False
            if line == "# Files":
                states["files_section"] = True
                states["not_target"] = False
                return False
            if line.startswith("# Finished Make data base"):
                states["database_finished"] = True
                states["files_section"] = False
                states["not_target"] = False
                return False
            if not states["files_section"]:
                return False
            if line == "# Not a target:":
                states["not_target"] = True
                return False
            if line.startswith(f"{expected_target}:"):
                states["probe_target_seen"] = states["probe_target_seen"] or not states["not_target"]
                states["not_target"] = False
                return False
            if line.startswith("init:"):
                states["explicit_init"] = states["explicit_init"] or not states["not_target"]
                states["not_target"] = False
                return False
            if line == ".DEFAULT:":
                states["explicit_default"] = states["explicit_default"] or not states["not_target"]
                states["not_target"] = False
                return False
            if line.strip():
                states["not_target"] = False
            return False

        return match

    def database_is_complete(states: dict[str, bool]) -> bool:
        return (
            states["database_started"]
            and not states["files_section"]
            and states["database_finished"]
            and states["probe_target_seen"]
        )

    def run_database_probe(overlay_path: Path, states: dict[str, bool]) -> subprocess.CompletedProcess[str]:
        completed, _ = _run_bounded_process_with_matcher(
            (
                make_executable,
                "-np",
                "-k",
                "-f",
                makefile.name,
                "-f",
                str(overlay_path),
                # The harmless probe goal avoids resolving ``init`` while
                # this assignment preserves Makefiles that condition rules
                # on the direct command's MAKECMDGOALS value.
                "MAKECMDGOALS=init",
                probe_target,
            ),
            cwd,
            database_matcher(states, probe_target),
            env=_make_probe_environment(),
        )
        return completed

    with tempfile.TemporaryDirectory(prefix="worktree-provisioner-make-") as directory:
        overlay_path = Path(directory) / "Makefile"
        overlay_path.write_text(overlay, encoding="utf-8")
        states = new_states()
        completed = run_database_probe(overlay_path, states)
    if completed.returncode == 0 and database_is_complete(states):
        if states["explicit_init"] or states["explicit_default"]:
            return direct, False
        return subprocess.CompletedProcess(
            completed.args,
            0,
            stdout="",
            stderr="",
        ), False
    return direct, False


def _make_probe_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["LC_ALL"] = "C"
    return environment


def _run_bounded_process(
    argv: tuple[str, ...], cwd: Path, *, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    completed, _ = _run_bounded_process_with_matcher(argv, cwd, None, env=env)
    return completed


def _run_bounded_process_with_matcher(
    argv: tuple[str, ...],
    cwd: Path,
    matcher: Callable[[str], bool] | None,
    *,
    env: dict[str, str] | None = None,
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
        env=env,
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

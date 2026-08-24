"""``make init`` bootstrap adapter.

The adapter only reports observable bootstrap state.  Whether bootstrap is
requested, how a failure affects a create operation, and whether the
repository is trusted are application/CLI policy decisions.
"""

from __future__ import annotations

import os
import re
import selectors
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final, Protocol, cast

from worktree_provisioner.application.contracts import BootstrapResult

_MAKE_COMMAND: Final[str] = "make"
_DIAGNOSTIC_LIMIT: Final[int] = 4096
_STREAM_CHUNK_SIZE: Final[int] = 8192
_MAKEFILE_NAMES: Final[tuple[str, ...]] = ("GNUmakefile", "makefile", "Makefile")
_STATIC_MAKEFILE_LIMIT: Final[int] = 1024 * 1024


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
        # Keep the public detection command stable.  A non-zero direct result
        # is only downgraded to ``skipped`` when this repository file is
        # statically obvious to contain no init target.
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
            detected, has_init_target = self._run_detection(detection_argv, target)
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
            if self.runner is None and _static_proves_init_absent(makefile):
                return _skipped()
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

    def _run_detection(self, argv: tuple[str, ...], cwd: Path) -> tuple[subprocess.CompletedProcess[str], bool]:
        """Run the sole dynamic detection command, ``make -n init``."""

        if self.runner is not None:
            # The injected runner is a test seam; production uses the direct
            # process path below and the static proof after its failure.
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
        return _run_direct_detection(self.make_executable, cwd)


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


_UNSAFE_MAKE_DIRECTIVE = re.compile(
    r"^(?:-?include|sinclude|if|ifdef|ifndef|ifeq|ifneq|else|endif|define|endef|"
    r"override|export|unexport|private|vpath|load|unload|undefine)\b",
    re.IGNORECASE,
)
_MAKE_ASSIGNMENT = re.compile(r"^(?P<name>[A-Za-z_][A-Za-z0-9_.-]*)\s*(?::=|\?=|\+=|!=|=)")
_MAKE_AUTHORITY_VARIABLES = frozenset({"MAKEFLAGS", "MAKEOVERRIDES", "GNUMAKEFLAGS", "MAKEFILES", "MAKEFILE_LIST"})


def _run_direct_detection(make_executable: str, cwd: Path) -> tuple[subprocess.CompletedProcess[str], bool]:
    """Run the sole dynamic target check without changing Make state."""

    direct = _run_bounded_process((make_executable, "-n", "init"), cwd)
    return direct, direct.returncode == 0


def _static_proves_init_absent(makefile: Path) -> bool:
    """Return true only for a plainly parseable file with no init rule.

    This is intentionally a narrow source scan, not a Make parser.  Any
    construct that can change the rule graph or make interpretation causes a
    conservative detection failure instead of a false ``skipped`` result.
    """

    # These variables alter the makefile graph or command-line state outside
    # this file.  A direct non-zero probe cannot prove that ``init`` is absent
    # while any of them is active, so fail closed.
    if any(name in os.environ for name in _MAKE_AUTHORITY_VARIABLES):
        return False
    try:
        with makefile.open("rb") as stream:
            source = stream.read(_STATIC_MAKEFILE_LIMIT + 1)
    except OSError:
        return False
    if len(source) > _STATIC_MAKEFILE_LIMIT or b"\0" in source:
        return False
    try:
        text = source.decode("utf-8")
    except UnicodeDecodeError:
        return False
    if "\ufeff" in text:
        return False

    saw_rule = False
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if _has_line_continuation(line):
            return False
        if line.startswith("\t"):
            if not saw_rule:
                return False
            if "$" in line or any(name in line for name in _MAKE_AUTHORITY_VARIABLES | {"origin"}):
                return False
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Inline comments cannot be parsed safely without reproducing Make's
        # escaping rules; ignoring their tail keeps this proof conservative.
        semantic = line.split("#", 1)[0].strip()
        if not semantic:
            continue
        if "$" in semantic or "%" in semantic:
            return False
        if _UNSAFE_MAKE_DIRECTIVE.match(semantic):
            return False
        assignment = _MAKE_ASSIGNMENT.match(semantic)
        if assignment is not None:
            if assignment.group("name") in _MAKE_AUTHORITY_VARIABLES:
                return False
            continue
        colon = semantic.find(":")
        if colon < 1:
            return False
        left = semantic[:colon].strip()
        if not left or "\\" in left:
            return False
        if left == ".PHONY":
            if any(token == "init" or _is_makefile_target(token) for token in semantic[colon + 1 :].split()):
                return False
            saw_rule = True
            continue
        if left.startswith("."):
            return False
        if "init" in left.split():
            return False
        # A rule that can remake the selected makefile means the direct
        # failure may depend on a generated/reloaded source.  It is not safe
        # to reinterpret that graph with a source-only absence proof.
        if any(_is_makefile_target(token) for token in left.split()):
            return False
        # Multiple unescaped colons in a normal rule header are ambiguous to
        # this deliberately small proof (double-colon rules are rejected as
        # well).  Let make's direct failure remain detection_failed instead of
        # guessing whether the text was a valid rule.
        if ":" in semantic[colon + 1 :]:
            return False
        saw_rule = True
    return True


def _is_makefile_target(token: str) -> bool:
    return token in _MAKEFILE_NAMES or Path(token).name in _MAKEFILE_NAMES


def _has_line_continuation(line: str) -> bool:
    candidate = line.rstrip()
    trailing_backslashes = len(candidate) - len(candidate.rstrip("\\"))
    return trailing_backslashes % 2 == 1


def _run_bounded_process(argv: tuple[str, ...], cwd: Path) -> subprocess.CompletedProcess[str]:
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

    return subprocess.CompletedProcess(
        list(argv),
        returncode,
        stdout=bytes(streams[stdout_fd]).decode("utf-8", errors="replace"),
        stderr=bytes(streams[stderr_fd]).decode("utf-8", errors="replace"),
    )


def _contains_make_target(output: str, target: str) -> bool:
    """Recognize the explicit synthetic output used by the runner seam."""

    return any(line.startswith(f"{target}:") for line in output.splitlines())


__all__ = [
    "BootstrapAdapterError",
    "BootstrapCliGateway",
    "MakeAdapterError",
    "MakeCliGateway",
    "MakeCommandError",
    "MakeGateway",
]

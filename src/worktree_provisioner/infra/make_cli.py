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
_DATABASE_OUTPUT_LIMIT: Final[int] = 1024 * 1024
_DATABASE_START_MARKER: Final[str] = "# Files"
_DATABASE_END_MARKER: Final[str] = "# Finished Make data base"


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
        # Keep the public detection command stable.  The direct command uses
        # Make's normal goal/prerequisite/remake semantics; the database probe
        # below supplies the structural target fact without parsing source.
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
            detected, runner_has_init_target = self._run_detection(detection_argv, target)
        except OSError as exc:
            return BootstrapResult(
                requested=True,
                status="detection_failed",
                command=detection_argv,
                exit_code=None,
                detail=_safe_bounded(str(exc)),
            )

        detection_detail = _diagnostic(detected.stdout, detected.stderr)
        if self.runner is not None:
            # The injectable runner is intentionally a narrow unit-test seam:
            # its synthetic stdout remains the only structural input there.
            # Production never uses this text heuristic.
            if detected.returncode != 0:
                return BootstrapResult(
                    requested=True,
                    status="detection_failed",
                    command=detection_argv,
                    exit_code=detected.returncode,
                    detail=detection_detail or "make init detection failed",
                )
            if not runner_has_init_target:
                return _skipped()
        else:
            try:
                database = _run_database_probe(self.make_executable, target)
            except OSError as exc:
                return BootstrapResult(
                    requested=True,
                    status="detection_failed",
                    command=detection_argv,
                    exit_code=None,
                    detail=_safe_bounded(str(exc)),
                )

            database_facts = _database_facts(database.stdout, "init")
            if database_facts is None:
                if detected.returncode != 0:
                    return BootstrapResult(
                        requested=True,
                        status="detection_failed",
                        command=detection_argv,
                        exit_code=detected.returncode,
                        detail=detection_detail or "make init database detection was inconclusive",
                    )
                # Direct Make evaluation is authoritative when it succeeds;
                # a structural probe with different built-in rules may fail
                # without invalidating a runnable direct target.
                database_facts = (False, False)
            if any(name in os.environ for name in _MAKE_AUTHORITY_VARIABLES):
                return BootstrapResult(
                    requested=True,
                    status="detection_failed",
                    command=detection_argv,
                    exit_code=detected.returncode,
                    detail="make environment altered target detection",
                )
            has_init_target, has_fallback_rule = database_facts
            if detected.returncode != 0:
                if (
                    not has_init_target
                    and not has_fallback_rule
                    and _is_plain_missing_target_failure(detected.stderr)
                    and _is_plain_missing_target_failure(database.stderr)
                ):
                    return _skipped()
                database_detail = _diagnostic(None, database.stderr)
                return BootstrapResult(
                    requested=True,
                    status="detection_failed",
                    command=detection_argv,
                    exit_code=detected.returncode,
                    detail=database_detail or detection_detail or "make init detection failed",
                )
            if not has_init_target:
                # A missing goal is a valid absence proof only when Make's
                # database parsed successfully.  A pre-existing path is also
                # a valid no-rule case: Make reports it as up to date even
                # though it has no database rule entry.
                if (target / "init").exists():
                    return _skipped()
                if has_fallback_rule:
                    return BootstrapResult(
                        requested=True,
                        status="detection_failed",
                        command=detection_argv,
                        exit_code=detected.returncode,
                        detail=detection_detail or "make init detection failed",
                    )
                # Direct Make evaluation succeeded.  A separate database
                # probe may have different MAKEFLAGS/goal semantics, so its
                # absence result must not suppress the real ``make init``.
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
            # process path below and the Make database probe after it.
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
        return _run_direct_detection(self.make_executable, cwd), False


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


_MAKE_AUTHORITY_VARIABLES = frozenset({"MAKEFLAGS", "MAKEOVERRIDES", "GNUMAKEFLAGS", "MAKEFILES", "MAKEFILE_LIST"})


def _run_direct_detection(make_executable: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run Make's normal dry-run graph evaluation for the ``init`` goal."""

    return _run_bounded_process((make_executable, "-n", "init"), cwd)


def _run_database_probe(make_executable: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Print Make's parsed database while preserving the direct goal context.

    The probe keeps ``init`` as its goal so Makefile conditionals observe the
    same ``MAKECMDGOALS`` and variable origins as direct detection.  ``-r`` /
    ``-R`` only remove built-in rules and variables, allowing user-defined
    fallback rules to be distinguished from the default Make database.
    """

    return _run_bounded_process(
        (make_executable, "-n", "-p", "-r", "-R", "init"),
        cwd,
        retain_limit=_DATABASE_OUTPUT_LIMIT,
    )


def _database_facts(output: str, target: str) -> tuple[bool, bool] | None:
    """Return explicit target and fallback facts from Make's database.

    The database is delimited by stable GNU Make markers.  Looking only at
    rule headers in that section avoids treating ``$(info init:)`` or a
    diagnostic as a target.  ``None`` means the output was truncated or was
    not a database, which is a detection failure rather than an absence proof.
    The fallback fact covers pattern/default rules whose failure can otherwise
    be mistaken for an absent ``init`` target.
    """

    lines = output.splitlines()
    starts = [index for index, line in enumerate(lines) if line == _DATABASE_START_MARKER]
    for start in reversed(starts):
        end = next(
            (index for index in range(start + 1, len(lines)) if lines[index].startswith(_DATABASE_END_MARKER)),
            None,
        )
        if end is None:
            continue
        has_target = False
        has_fallback_rule = False

        implicit_start = next(
            (index for index in range(start - 1, -1, -1) if lines[index] == "# Implicit Rules"),
            None,
        )
        if implicit_start is not None:
            for line in lines[implicit_start + 1 : start]:
                if line and not line.startswith("#") and not line.startswith("\t") and ":" in line:
                    header, _, _ = line.partition(":")
                    if any(_pattern_matches_target(pattern, target) for pattern in header.split()):
                        has_fallback_rule = True

        current_header: str | None = None
        current_has_commands = False
        not_a_target = False
        for line in lines[start + 1 : end]:
            if line == "# Not a target:":
                if current_header == ".DEFAULT" and current_has_commands:
                    has_fallback_rule = True
                not_a_target = True
                current_header = None
                current_has_commands = False
                continue
            if not line or line.startswith("#") or line.startswith("\t"):
                if line.startswith(("#  commands to execute", "#  recipe to execute")) and current_header == ".DEFAULT":
                    current_has_commands = True
                continue
            header, separator, _ = line.partition(":")
            if not separator:
                continue
            if not_a_target:
                not_a_target = False
                continue
            if current_header == ".DEFAULT" and current_has_commands:
                has_fallback_rule = True
            current_header = header
            current_has_commands = False
            if header.startswith("."):
                continue
            if target in header.split():
                has_target = True
        if current_header == ".DEFAULT" and current_has_commands:
            has_fallback_rule = True
        return has_target, has_fallback_rule
    return None


def _pattern_matches_target(pattern: str, target: str) -> bool:
    """Return whether a GNU Make pattern can select ``target``.

    The database probe only needs the target-selection portion of implicit
    rule matching.  A ``%`` stem is sufficient here; prerequisite feasibility
    remains authoritative in the direct Make result.
    """

    if "%" not in pattern:
        return False
    prefix, suffix = pattern.split("%", 1)
    if not target.startswith(prefix):
        return False
    return target[len(prefix) :].endswith(suffix)


def _is_plain_missing_target_failure(stderr: str) -> bool:
    """Recognize Make's ordinary missing-target diagnostic.

    Parse and expansion failures include a Makefile location (for example,
    ``Makefile:1: ***``).  A missing prerequisite also names ``needed by``.
    Such diagnostics must remain detection failures; only the location-free
    fatal diagnostic for the requested goal can prove target absence here.
    """

    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if any(re.search(r"^[^:\n]+:\d+(?::\d+)?:\s", line) for line in lines):
        return False
    return any(
        re.search(r"^[^:\n]+:\s+\*\*\*\s+No rule to make target", line)
        and "needed by" not in line
        and re.search(r"(?:[`']init[`']|\binit\b)", line)
        for line in lines
    )


def _database_has_target(output: str, target: str) -> bool | None:
    """Compatibility wrapper for callers that only need target presence."""

    facts = _database_facts(output, target)
    return None if facts is None else facts[0]


def _run_bounded_process(
    argv: tuple[str, ...], cwd: Path, *, retain_limit: int = _DIAGNOSTIC_LIMIT
) -> subprocess.CompletedProcess[str]:
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
                if len(retained) < retain_limit:
                    retained.extend(chunk[: retain_limit - len(retained)])
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

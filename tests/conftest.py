"""Shared fixtures for executable worktree-provisioner contract tests.

The fixtures intentionally create isolated Git repositories under pytest's
temporary directory.  They do not use the developer's checkout or the
configured production worktree root.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import pytest

from worktree_provisioner.application.contracts import BootstrapResult, BootstrapStatus


@dataclass(frozen=True)
class TempGitRepository:
    """A temporary repository with a committed file and local identity."""

    path: Path

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ["git", *args],
            cwd=self.path,
            capture_output=True,
            text=True,
            check=False,
        )
        if check and completed.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed:\n{completed.stderr}")
        return completed


@dataclass(frozen=True)
class CliResult:
    """Subprocess result with the command and isolated environment retained."""

    command: tuple[str, ...]
    completed: subprocess.CompletedProcess[str]
    environment: dict[str, str]

    @property
    def returncode(self) -> int:
        return self.completed.returncode

    @property
    def stdout(self) -> str:
        return self.completed.stdout

    @property
    def stderr(self) -> str:
        return self.completed.stderr


@dataclass(frozen=True)
class FakeGitRecord:
    path: Path
    head: str | None = "0123456789abcdef"
    branch: str | None = "main"
    detached: bool = False
    bare: bool = False
    locked: bool = False
    lock_reason: str | None = None


@dataclass
class FakeGitGateway:
    """Call-recording Git gateway foundation for application-level tests.

    This deliberately mirrors the narrow P3 port names from the design
    document without importing production modules that do not exist in P1.
    Later phases can use it for deterministic retry, refresh, and force tests.
    """

    checkout_root: Path
    current_branch: str | None = "main"
    records: list[FakeGitRecord] = field(default_factory=list)
    branches: set[str] = field(default_factory=lambda: {"main"})
    valid_refs: set[str] | None = None
    calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = field(default_factory=list)
    add_error: BaseException | None = None
    remove_error: BaseException | None = None

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def resolve_checkout_root(self, path: Path) -> Path:
        self._record("resolve_checkout_root", path)
        return self.checkout_root

    def current_branch_or_none(self, repo_root: Path) -> str | None:
        self._record("current_branch_or_none", repo_root)
        return self.current_branch

    def local_branch_exists(self, repo_root: Path, branch: str) -> bool:
        self._record("local_branch_exists", repo_root, branch)
        return branch in self.branches

    def check_branch_ref(self, repo_root: Path, branch: str) -> bool:
        self._record("check_branch_ref", repo_root, branch)
        if self.valid_refs is None:
            return True
        return branch in self.valid_refs

    def worktree_list(self, repo_root: Path) -> list[FakeGitRecord]:
        self._record("worktree_list", repo_root)
        return list(self.records)

    def add_worktree(self, repo_root: Path, *, path: Path, branch: str) -> None:
        self._record("add_worktree", repo_root, path=path, branch=branch)
        if self.add_error is not None:
            raise self.add_error
        self.branches.add(branch)
        self.records.append(FakeGitRecord(path=path, branch=branch))

    def remove_worktree(self, repo_root: Path, *, path: Path, force: bool) -> None:
        self._record("remove_worktree", repo_root, path=path, force=force)
        if self.remove_error is not None:
            raise self.remove_error
        self.records = [record for record in self.records if record.path != path]


@dataclass(frozen=True)
class FakeBootstrapResult:
    status: str = "skipped"
    exit_code: int | None = None
    detail: str | None = None


@dataclass
class FakeBootstrapGateway:
    """Deterministic bootstrap gateway foundation with call recording."""

    result: FakeBootstrapResult = field(default_factory=FakeBootstrapResult)
    calls: list[Path] = field(default_factory=list)

    def run_make_init_if_available(self, worktree_path: Path) -> BootstrapResult:
        self.calls.append(worktree_path)
        status = cast(BootstrapStatus, self.result.status)
        command: tuple[str, ...] | None
        if status in {"disabled", "skipped"}:
            command = None
        elif status == "detection_failed":
            command = ("make", "-n", "init")
        else:
            command = ("make", "init")
        return BootstrapResult(
            requested=status != "disabled",
            status=status,
            command=command,
            exit_code=self.result.exit_code,
            detail=self.result.detail,
        )


@dataclass
class FakeFilesystemGateway:
    """No-follow filesystem gateway foundation for cleanup tests."""

    kinds: dict[Path, str] = field(default_factory=dict)
    existing: set[Path] = field(default_factory=set)
    calls: list[tuple[str, Path]] = field(default_factory=list)
    remove_error: BaseException | None = None

    def lstat_kind(self, path: Path) -> str:
        self.calls.append(("lstat_kind", path))
        return self.kinds.get(path, "missing")

    def path_exists_no_follow(self, path: Path) -> bool:
        self.calls.append(("path_exists_no_follow", path))
        return path in self.existing

    def ensure_directory(self, path: Path) -> None:
        self.calls.append(("ensure_directory", path))
        self.existing.add(path)

    def remove_target_no_follow(self, path: Path) -> None:
        self.calls.append(("remove_target_no_follow", path))
        if self.remove_error is not None:
            raise self.remove_error
        self.existing.discard(path)


def _run_git(path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=path,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed:\n{completed.stderr}")
    return completed


def _prepare_repository(path: Path, *, makefile: str | None = None) -> TempGitRepository:
    path.mkdir(parents=True)
    _run_git(path, "init", "-b", "main")
    _run_git(path, "config", "user.name", "worktree-provisioner tests")
    _run_git(path, "config", "user.email", "worktree-provisioner-tests@example.invalid")
    _run_git(path, "config", "gc.auto", "0")
    _run_git(path, "config", "maintenance.auto", "false")
    (path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    if makefile is not None:
        (path / "Makefile").write_text(makefile, encoding="utf-8")
    _run_git(path, "add", "--all")
    _run_git(path, "commit", "-m", "initial test commit")
    return TempGitRepository(path)


def _isolated_environment(overrides: dict[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("WORKTREE_PROVISIONER_ROOT", None)
    environment.pop("SPEC_DOCK_WORKTREE_ROOT", None)
    if overrides:
        environment.update(overrides)
    return environment


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> TempGitRepository:
    return _prepare_repository(tmp_path / "sample-repo")


@pytest.fixture
def git_repo_factory(tmp_path: Path) -> Callable[..., TempGitRepository]:
    def factory(name: str = "sample-repo", *, makefile: str | None = None) -> TempGitRepository:
        return _prepare_repository(tmp_path / name, makefile=makefile)

    return factory


@pytest.fixture
def central_root(tmp_path: Path) -> Path:
    return tmp_path / "managed-worktrees"


@pytest.fixture
def exact_environment() -> dict[str, str]:
    """Return a host-root-free environment for every CLI subprocess."""

    return _isolated_environment()


@pytest.fixture
def cli_runner(exact_environment: dict[str, str]) -> Callable[..., CliResult]:
    def run_cli(
        *args: str,
        repo: Path | None = None,
        root: Path | str | None = None,
        environment: dict[str, str] | None = None,
    ) -> CliResult:
        command = [sys.executable, "-m", "worktree_provisioner", *args]
        if repo is not None:
            command.extend(["--repo", str(repo)])
        if root is not None:
            command.extend(["--root", str(root)])
        env = dict(exact_environment)
        if environment:
            env.update(environment)
        completed = subprocess.run(
            command,
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        return CliResult(tuple(command), completed, env)

    return run_cli


@pytest.fixture
def symlink_capability(tmp_path: Path) -> Path:
    target = tmp_path / "symlink-target"
    target.mkdir()
    link = tmp_path / "symlink-probe"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink support is unavailable: {exc}")
    finally:
        if link.is_symlink():
            link.unlink()
    return target


@pytest.fixture
def fake_git_gateway(tmp_path: Path) -> FakeGitGateway:
    repo_root = tmp_path / "fake-repo"
    repo_root.mkdir()
    return FakeGitGateway(checkout_root=repo_root, records=[FakeGitRecord(path=repo_root)])


@pytest.fixture
def fake_bootstrap_gateway() -> FakeBootstrapGateway:
    return FakeBootstrapGateway()


@pytest.fixture
def fake_filesystem_gateway() -> FakeFilesystemGateway:
    return FakeFilesystemGateway()


@pytest.fixture
def fake_cli_on_path(tmp_path: Path) -> Iterator[Path]:
    """Install a recording fake executable on PATH for wrapper tests."""

    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    executable = bin_dir / "worktree-provisioner"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "print(json.dumps(sys.argv[1:]))\n"
        "print('fake stderr', file=sys.stderr)\n"
        "raise SystemExit(int(os.environ.get('FAKE_WTP_EXIT', '0')))\n",
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    yield bin_dir


@pytest.fixture
def json_loads() -> Callable[[str], dict[str, Any]]:
    def load(output: str) -> dict[str, Any]:
        decoder = json.JSONDecoder()
        normalized = output.lstrip()
        assert normalized, "expected one JSON document on stdout, got empty output"
        try:
            payload, end = decoder.raw_decode(normalized)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"stdout is not a JSON document: {output!r}") from exc
        assert not normalized[end:].strip(), "stdout must contain exactly one JSON document"
        assert isinstance(payload, dict)
        return payload

    return load

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

_LABEL_RE = re.compile(r"^[a-z0-9-]+$")
_MAX_ATTEMPTS = 10_000
_RETRYABLE_GIT_ERRORS = ("already exists", "is already checked out", "a branch named")
_ROOT_ENV = "WORKTREE_PROVISIONER_ROOT"
_LEGACY_ROOT_ENV = "SPEC_DOCK_WORKTREE_ROOT"

BootstrapStatus = Literal["skipped", "succeeded", "failed", "detection_failed"]


@dataclass(frozen=True)
class GitWorktreeRecord:
    path: Path
    head: str | None
    branch: str | None


@dataclass(frozen=True)
class BootstrapResult:
    status: BootstrapStatus
    command: str | None
    exit_code: int | None
    warnings: list[str]


@dataclass(frozen=True)
class CreateResult:
    id: str
    main_worktree_path: Path
    container_path: Path
    worktree_path: Path
    branch_name: str
    bootstrap: BootstrapResult


def create_worktree(*, repo: Path, root: str | None, label: str | None) -> CreateResult:
    normalized_label = _normalize_label(label)
    central_root = _resolve_root(root)
    repo_root = _resolve_repo_root(repo)
    branch_prefix = _current_branch(repo_root)
    if branch_prefix is None:
        raise RuntimeError("worktree create requires a named current branch; detached HEAD is not supported")

    records = _worktree_list(repo_root)
    if not records:
        raise RuntimeError("git worktree list returned no worktrees")
    main_worktree = records[0].path
    repo_basename = main_worktree.name
    container = central_root / repo_basename
    known_paths = {_canonical_path(record.path) for record in records}
    last_id = ""
    last_reason = "no candidates attempted"

    for index in range(1, _MAX_ATTEMPTS + 1):
        worktree_id = _candidate_id(normalized_label, index)
        last_id = worktree_id
        worktree_path = container / f"{repo_basename}-{worktree_id}"
        branch_name = f"{branch_prefix}-{worktree_id}"
        collision = _preflight_collision(repo_root, worktree_path, branch_name, known_paths)
        if collision is not None:
            last_reason = collision
            continue

        try:
            container.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            state = _artifact_state(repo_root, worktree_path, branch_name, known_paths, refresh=False)
            raise RuntimeError(
                "failed to create worktree container: "
                f"root={central_root} container={container} {state}\n{exc}"
            ) from exc

        try:
            _git(repo_root, "worktree", "add", "-b", branch_name, str(worktree_path))
        except RuntimeError as exc:
            message = str(exc)
            if _is_retryable_git_error(message):
                known_paths = {_canonical_path(record.path) for record in _worktree_list(repo_root)}
                last_reason = f"retryable git collision: {message}"
                continue
            state = _artifact_state(repo_root, worktree_path, branch_name, known_paths, refresh=True)
            raise RuntimeError(
                "git worktree add failed for non-retryable reason: "
                f"id={worktree_id} path={worktree_path} branch={branch_name} {state}\n{message}"
            ) from exc

        return CreateResult(
            id=worktree_id,
            main_worktree_path=main_worktree,
            container_path=container,
            worktree_path=worktree_path,
            branch_name=branch_name,
            bootstrap=_run_make_init_if_available(worktree_path),
        )

    mode = "label" if normalized_label is not None else "auto"
    raise RuntimeError(
        "worktree create exhausted candidate attempts: "
        f"mode={mode} last_id={last_id} container={container} reason={last_reason}"
    )


def _resolve_repo_root(repo: Path) -> Path:
    candidate = repo.expanduser().resolve(strict=False)
    completed = _git(candidate, "rev-parse", "--show-toplevel")
    return Path(completed.stdout.strip())


def _resolve_root(explicit_root: str | None) -> Path:
    raw = explicit_root
    if raw is None:
        raw = os.environ.get(_ROOT_ENV)
    if raw is None:
        raw = os.environ.get(_LEGACY_ROOT_ENV)
    if raw is None or not raw.strip():
        raise RuntimeError(
            f"worktree root is required; pass --root or set {_ROOT_ENV}. "
            f"{_LEGACY_ROOT_ENV} is accepted for SpecDock migration compatibility"
        )
    expanded = Path(raw).expanduser()
    resolved = expanded.resolve(strict=False)
    if not expanded.is_absolute():
        raise RuntimeError(f"invalid worktree root: raw={raw!r} resolved={resolved} cause=path is relative")
    if expanded.exists():
        if not expanded.is_dir():
            raise RuntimeError(f"invalid worktree root: raw={raw!r} resolved={resolved} cause=path is not a directory")
        return expanded
    if expanded.is_symlink():
        raise RuntimeError(f"invalid worktree root: raw={raw!r} resolved={resolved} cause=path is a broken symlink")
    return expanded


def _normalize_label(label: str | None) -> str | None:
    if label is None:
        return None
    if not label or _LABEL_RE.fullmatch(label) is None:
        raise RuntimeError("invalid worktree label: use lowercase letters, digits, and hyphens only")
    return label


def _candidate_id(label: str | None, index: int) -> str:
    if label is None:
        return f"wt{index}"
    return label if index == 1 else f"{label}{index}"


def _current_branch(repo_root: Path) -> str | None:
    branch = _git(repo_root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    return None if not branch or branch == "HEAD" else branch


def _worktree_list(repo_root: Path) -> list[GitWorktreeRecord]:
    return _parse_worktree_porcelain(_git(repo_root, "worktree", "list", "--porcelain").stdout)


def _parse_worktree_porcelain(text: str) -> list[GitWorktreeRecord]:
    records: list[GitWorktreeRecord] = []
    current: dict[str, str] = {}

    def flush() -> None:
        if "path" not in current:
            return
        branch = current.get("branch")
        prefix = "refs/heads/"
        if branch and branch.startswith(prefix):
            branch = branch[len(prefix) :]
        records.append(GitWorktreeRecord(path=Path(current["path"]), head=current.get("head"), branch=branch))

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            current = {}
        elif line.startswith("worktree "):
            flush()
            current = {"path": line.removeprefix("worktree ")}
        elif line.startswith("HEAD "):
            current["head"] = line.removeprefix("HEAD ")
        elif line.startswith("branch "):
            current["branch"] = line.removeprefix("branch ")
    flush()
    return records


def _preflight_collision(
    repo_root: Path, worktree_path: Path, branch_name: str, known_paths: set[str]
) -> str | None:
    if _canonical_path(worktree_path) in known_paths:
        return "worktree record already exists"
    if worktree_path.exists():
        return "worktree path already exists"
    if _git(repo_root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}", check=False).returncode == 0:
        return "branch already exists"
    if _git(repo_root, "check-ref-format", "--branch", branch_name, check=False).returncode != 0:
        raise RuntimeError(f"generated worktree branch is invalid: {branch_name}")
    return None


def _run_make_init_if_available(worktree_path: Path) -> BootstrapResult:
    if shutil.which("make") is None:
        return BootstrapResult(
            status="detection_failed",
            command="make -n init",
            exit_code=None,
            warnings=["make init detection failed: make command not found"],
        )
    dry_run = subprocess.run(
        ["make", "-n", "init"], cwd=worktree_path, capture_output=True, text=True, check=False
    )
    if dry_run.returncode != 0:
        stderr = (dry_run.stderr or "").strip()
        lowered = stderr.lower()
        missing_fragments = (
            "no rule to make target 'init'",
            "no rule to make target `init'",
            "no rule to make target init",
            "no targets specified and no makefile found",
            "no makefile found",
        )
        if any(fragment in lowered for fragment in missing_fragments):
            return BootstrapResult(status="skipped", command=None, exit_code=None, warnings=[])
        return BootstrapResult(
            status="detection_failed",
            command="make -n init",
            exit_code=dry_run.returncode,
            warnings=[f"make init detection failed: {stderr or 'unknown error'}"],
        )
    run = subprocess.run(["make", "init"], cwd=worktree_path, capture_output=True, text=True, check=False)
    if run.returncode == 0:
        return BootstrapResult(status="succeeded", command="make init", exit_code=0, warnings=[])
    detail = (run.stderr or "").strip() or (run.stdout or "").strip() or "unknown error"
    return BootstrapResult(
        status="failed",
        command="make init",
        exit_code=run.returncode,
        warnings=[f"make init failed: {detail}"],
    )


def _git(repo_root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    if shutil.which("git") is None:
        raise RuntimeError("git command not found")
    command = ["git", *args]
    completed = subprocess.run(command, cwd=repo_root, capture_output=True, text=True, check=False)
    if check and completed.returncode != 0:
        output_parts = ((completed.stderr or "").strip(), (completed.stdout or "").strip())
        details = "\n".join(part for part in output_parts if part)
        raise RuntimeError(f"git failed: {' '.join(command)}\n{details}")
    return completed


def _canonical_path(path: Path) -> str:
    return str(path.expanduser().resolve(strict=False))


def _is_retryable_git_error(message: str) -> bool:
    lowered = message.lower()
    return any(fragment in lowered for fragment in _RETRYABLE_GIT_ERRORS)


def _artifact_state(
    repo_root: Path,
    worktree_path: Path,
    branch_name: str,
    known_paths: set[str],
    *,
    refresh: bool,
) -> str:
    record_paths = known_paths
    if refresh:
        with contextlib.suppress(RuntimeError):
            record_paths = {_canonical_path(record.path) for record in _worktree_list(repo_root)}
    branch_exists = _git(
        repo_root, "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}", check=False
    ).returncode == 0
    return (
        f"artifact_state=path_exists:{worktree_path.exists()},"
        f"branch_exists:{branch_exists},record_exists:{_canonical_path(worktree_path) in record_paths}"
    )

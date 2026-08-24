from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(command, cwd=cwd, env=merged, capture_output=True, text=True, check=False)


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    completed = run(["git", *args], cwd=repo)
    assert completed.returncode == 0, completed.stderr
    return completed


def prepare_repo(path: Path, *, makefile: str | None = None) -> Path:
    path.mkdir()
    if makefile is not None:
        (path / "Makefile").write_text(makefile, encoding="utf-8")
    git(path, "init")
    git(path, "config", "gc.auto", "0")
    git(path, "config", "maintenance.auto", "false")
    (path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    git(path, "add", "-A")
    git(path, "-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-m", "init")
    return path


def cli(repo: Path, root: Path | str | None, *extra: str, exact_env: bool = False) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "worktree_provisioner", "create", *extra, "--repo", str(repo)]
    env = os.environ.copy()
    if exact_env:
        env.pop("WORKTREE_PROVISIONER_ROOT", None)
        env.pop("SPEC_DOCK_WORKTREE_ROOT", None)
    if root is not None:
        command.extend(["--root", str(root)])
    return subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True, check=False)


def test_create_auto_id_and_branch(tmp_path: Path) -> None:
    repo = prepare_repo(tmp_path / "sample-repo")
    root = tmp_path / "worktrees"
    completed = cli(repo, root)
    branch = git(repo, "branch", "--show-current").stdout.strip()
    target = root / "sample-repo" / "sample-repo-wt1"
    assert completed.returncode == 0, completed.stderr
    assert f"id=wt1 branch={branch}-wt1 path={target}" in completed.stdout
    assert target.is_dir()
    assert str(target.resolve()) in git(repo, "worktree", "list", "--porcelain").stdout


def test_create_retries_label_and_branch_collisions(tmp_path: Path) -> None:
    repo = prepare_repo(tmp_path / "sample-repo")
    root = tmp_path / "worktrees"
    first = cli(repo, root, "feature")
    second = cli(repo, root, "feature")
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "id=feature " in first.stdout
    assert "id=feature2 " in second.stdout


@pytest.mark.parametrize("label", ["Issue_1", "issue.1", "issue/1", "issue 1", "UPPER"])
def test_create_rejects_invalid_label_without_side_effects(tmp_path: Path, label: str) -> None:
    repo = prepare_repo(tmp_path / "sample-repo")
    root = tmp_path / "worktrees"
    completed = cli(repo, root, label)
    assert completed.returncode == 1
    assert "invalid worktree label" in completed.stderr
    assert not root.exists()


def test_create_requires_root_without_side_effects(tmp_path: Path) -> None:
    repo = prepare_repo(tmp_path / "sample-repo")
    completed = cli(repo, None, exact_env=True)
    assert completed.returncode == 1
    assert "worktree root is required" in completed.stderr
    assert "-wt1" not in git(repo, "branch", "--list").stdout


def test_create_rejects_relative_root(tmp_path: Path) -> None:
    repo = prepare_repo(tmp_path / "sample-repo")
    completed = cli(repo, "relative/worktrees")
    assert completed.returncode == 1
    assert "cause=path is relative" in completed.stderr


def test_create_uses_legacy_spec_dock_environment(tmp_path: Path) -> None:
    repo = prepare_repo(tmp_path / "sample-repo")
    root = tmp_path / "legacy-root"
    command = [sys.executable, "-m", "worktree_provisioner", "create", "legacy", "--repo", str(repo)]
    env = os.environ.copy()
    env.pop("WORKTREE_PROVISIONER_ROOT", None)
    env["SPEC_DOCK_WORKTREE_ROOT"] = str(root)
    completed = subprocess.run(command, cwd=repo, env=env, capture_output=True, text=True, check=False)
    assert completed.returncode == 0, completed.stderr
    assert (root / "sample-repo" / "sample-repo-legacy").is_dir()


def test_create_normalizes_namespace_from_linked_worktree(tmp_path: Path) -> None:
    repo = prepare_repo(tmp_path / "sample-repo")
    root = tmp_path / "worktrees"
    first = cli(repo, root, "outer")
    assert first.returncode == 0, first.stderr
    linked = root / "sample-repo" / "sample-repo-outer"
    second = cli(linked, root, "inner")
    expected = root / "sample-repo" / "sample-repo-inner"
    assert second.returncode == 0, second.stderr
    assert f"path={expected}" in second.stdout
    assert expected.is_dir()


def test_create_rejects_detached_head(tmp_path: Path) -> None:
    repo = prepare_repo(tmp_path / "sample-repo")
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "checkout", "--detach", head)
    completed = cli(repo, tmp_path / "worktrees")
    assert completed.returncode == 1
    assert "detached HEAD is not supported" in completed.stderr


def test_create_runs_make_init(tmp_path: Path) -> None:
    repo = prepare_repo(tmp_path / "sample-repo", makefile="init:\n\t@echo initialized > .init-ran\n")
    root = tmp_path / "worktrees"
    completed = cli(repo, root, "setup")
    target = root / "sample-repo" / "sample-repo-setup"
    assert completed.returncode == 0, completed.stderr
    assert "bootstrap status=succeeded" in completed.stdout
    assert (target / ".init-ran").read_text(encoding="utf-8").strip() == "initialized"


def test_create_keeps_worktree_when_make_init_fails(tmp_path: Path) -> None:
    repo = prepare_repo(tmp_path / "sample-repo", makefile="init:\n\t@exit 7\n")
    root = tmp_path / "worktrees"
    completed = cli(repo, root, "setup")
    assert completed.returncode == 0, completed.stderr
    assert "bootstrap status=failed" in completed.stdout
    assert "warning: make init failed" in completed.stderr
    assert (root / "sample-repo" / "sample-repo-setup").is_dir()


def test_create_json_output(tmp_path: Path) -> None:
    repo = prepare_repo(tmp_path / "sample-repo")
    root = tmp_path / "worktrees"
    completed = cli(repo, root, "json-case", "--json")
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0, completed.stderr
    assert payload["status"] == "ok"
    assert payload["operation"] == "create"
    assert payload["result"]["id"] == "json-case"
    assert payload["result"]["worktree_path"] == str(root / "sample-repo" / "sample-repo-json-case")


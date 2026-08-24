from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from conftest import CliResult, TempGitRepository


def _payload(result: CliResult, json_loads: Callable[[str], dict[str, Any]]) -> dict[str, Any]:
    return json_loads(result.stdout)


def test_top_level_help_exposes_four_commands(cli_runner) -> None:
    result = cli_runner("--help")

    assert result.returncode == 0, result.stderr
    for command in ("create", "list", "show", "remove"):
        assert command in result.stdout
    assert "delete" not in result.stdout


def test_legacy_environment_alone_is_root_required(
    temp_git_repo: TempGitRepository, tmp_path: Path, cli_runner, json_loads
) -> None:
    legacy_root = tmp_path / "legacy-root"
    result = cli_runner(
        "create",
        "legacy",
        "--json",
        repo=temp_git_repo.path,
        environment={"SPEC_DOCK_WORKTREE_ROOT": str(legacy_root)},
    )

    payload = _payload(result, json_loads)
    assert result.returncode == 1
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "root_required"
    assert not legacy_root.exists()


def test_basic_create_uses_main_namespace_and_current_branch(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    result = cli_runner("create", "--json", repo=temp_git_repo.path, root=central_root)
    payload = _payload(result, json_loads)

    branch = temp_git_repo.git("branch", "--show-current").stdout.strip()
    expected_path = central_root / temp_git_repo.path.name / f"{temp_git_repo.path.name}-wt1"
    assert result.returncode == 0, result.stderr
    assert payload["status"] == "ok"
    assert payload["operation"] == "create"
    assert payload["result"]["id"] == "wt1"
    assert payload["result"]["branch"] == f"{branch}-wt1"
    assert payload["result"]["worktree_path"] == str(expected_path)
    assert payload["result"]["worktree_path"].startswith("/")
    assert expected_path.is_dir()


def test_linked_checkout_uses_main_namespace_and_invocation_branch(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    first = cli_runner(
        "create",
        "outer",
        "--no-bootstrap",
        "--json",
        repo=temp_git_repo.path,
        root=central_root,
    )
    assert first.returncode == 0, first.stderr

    linked = central_root / temp_git_repo.path.name / f"{temp_git_repo.path.name}-outer"
    linked_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=linked,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    second = cli_runner(
        "create",
        "inner",
        "--no-bootstrap",
        "--json",
        repo=linked,
        root=central_root,
    )
    payload = _payload(second, json_loads)
    expected_path = central_root / temp_git_repo.path.name / f"{temp_git_repo.path.name}-inner"

    assert second.returncode == 0, second.stderr
    assert payload["result"]["worktree_path"] == str(expected_path)
    assert payload["result"]["branch"] == f"{linked_branch}-inner"
    assert expected_path.is_dir()
    assert payload["result"]["container_path"] == str(central_root / temp_git_repo.path.name)


def test_bootstrap_failure_is_partial_nonzero_and_retained(
    git_repo_factory, central_root: Path, cli_runner, json_loads
) -> None:
    repo = git_repo_factory(makefile="init:\n\t@exit 7\n")
    result = cli_runner("create", "setup", "--json", repo=repo.path, root=central_root)
    payload = _payload(result, json_loads)
    target = central_root / repo.path.name / f"{repo.path.name}-setup"

    assert result.returncode == 1
    assert payload["status"] == "partial"
    assert payload["result"]["worktree_path"] == str(target)
    assert payload["result"]["bootstrap"]["status"] == "failed"
    assert payload["error"]["code"] == "bootstrap_failed"
    assert target.is_dir()
    assert repo.git("branch", "--list", "main-setup").stdout.strip()


def test_json_success_has_common_envelope_and_clean_stderr(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    result = cli_runner("create", "json-case", "--json", repo=temp_git_repo.path, root=central_root)
    payload = _payload(result, json_loads)

    assert result.returncode == 0, result.stderr
    assert set(payload) == {"schema_version", "status", "operation", "result", "error", "warnings"}
    assert payload["schema_version"] == 1
    assert payload["status"] == "ok"
    assert payload["operation"] == "create"
    assert payload["error"] is None
    assert payload["warnings"] == []
    assert isinstance(payload["result"]["artifacts"]["container_exists"], bool)
    assert result.stderr == ""


def test_cli_runner_removes_host_root_variables(temp_git_repo: TempGitRepository, cli_runner, json_loads) -> None:
    result = cli_runner("create", "--json", repo=temp_git_repo.path)
    payload = _payload(result, json_loads)

    assert "WORKTREE_PROVISIONER_ROOT" not in result.environment
    assert "SPEC_DOCK_WORKTREE_ROOT" not in result.environment
    assert result.returncode == 1
    assert payload["error"]["code"] == "root_required"

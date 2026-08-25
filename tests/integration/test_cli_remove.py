from __future__ import annotations

from pathlib import Path
from typing import cast

from conftest import CliResult, TempGitRepository  # type: ignore[import-not-found]


def _payload(result: CliResult, json_loads: object) -> dict[str, object]:
    assert callable(json_loads)
    return json_loads(result.stdout)  # type: ignore[operator]


def _mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value)


def _add_worktree(repo: TempGitRepository, path: Path, branch: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    repo.git("worktree", "add", "-b", branch, str(path))
    return path


def test_remove_success_reports_mutation_facts_and_keeps_local_branch(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    target = _add_worktree(
        temp_git_repo,
        central_root / temp_git_repo.path.name / f"{temp_git_repo.path.name}-remove-me",
        "remove-me",
    )

    result = cli_runner("remove", str(target), "--json", repo=temp_git_repo.path, root=central_root)
    payload = _payload(result, json_loads)
    result_payload = _mapping(payload["result"])

    assert result.returncode == 0, result.stderr
    assert payload["status"] == "ok"
    assert payload["operation"] == "remove"
    assert result_payload["removed_record"] is True
    assert result_payload["removed_directory"] is True
    assert result_payload["branch_deleted"] is False
    assert not target.exists()
    assert "remove-me" in temp_git_repo.git("branch", "--list").stdout


def test_remove_by_stable_id_is_supported(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    target = _add_worktree(
        temp_git_repo,
        central_root / temp_git_repo.path.name / f"{temp_git_repo.path.name}-stable",
        "stable-target",
    )

    result = cli_runner("remove", "stable", "--json", repo=temp_git_repo.path, root=central_root)
    payload = _payload(result, json_loads)
    result_payload = _mapping(payload["result"])
    resolved_target = _mapping(result_payload["resolved_target"])

    assert result.returncode == 0, result.stderr
    assert result_payload["target"] == "stable"
    assert resolved_target["path"] == str(target)


def test_remove_nested_managed_worktree_uses_relative_descendant_path(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    target = _add_worktree(
        temp_git_repo,
        central_root / temp_git_repo.path.name / "nested" / f"{temp_git_repo.path.name}-nested",
        "nested-target",
    )

    result = cli_runner("remove", str(target), "--json", repo=temp_git_repo.path, root=central_root)
    payload = _payload(result, json_loads)
    result_payload = _mapping(payload["result"])

    assert result.returncode == 0, result.stderr
    assert payload["status"] == "ok"
    assert result_payload["removed_record"] is True
    assert result_payload["removed_directory"] is True
    assert not target.exists()
    assert "nested-target" in temp_git_repo.git("branch", "--list").stdout

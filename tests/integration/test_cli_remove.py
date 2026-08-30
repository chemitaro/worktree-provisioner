from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
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


@pytest.mark.parametrize("force", [False, True])
def test_nested_managed_worktree_is_observable_but_blocked_before_mutation(
    temp_git_repo: TempGitRepository,
    central_root: Path,
    tmp_path: Path,
    cli_runner,
    json_loads,
    force: bool,
) -> None:
    target = _add_worktree(
        temp_git_repo,
        central_root / temp_git_repo.path.name / "nested" / f"{temp_git_repo.path.name}-nested",
        "nested-target",
    )
    external = tmp_path / "external-nested-target"
    external.mkdir()
    sentinel = external / "sentinel"
    sentinel.write_text("must remain\n", encoding="utf-8")

    listed = cli_runner("list", "--json", repo=temp_git_repo.path, root=central_root)
    listed_payload = _payload(listed, json_loads)
    listed_result = _mapping(listed_payload["result"])
    listed_worktrees = cast(list[dict[str, object]], listed_result["worktrees"])
    listed_record = next(record for record in listed_worktrees if record["path"] == str(target))
    assert listed.returncode == 0, listed.stderr
    assert listed_record["managed"] is True
    assert listed_record["origin"] == "managed_namespace"
    assert listed_record["removable"] is False
    assert listed_record["remove_blockers"] == ["nested_target_unsupported"]

    shown = cli_runner("show", "nested", "--json", repo=temp_git_repo.path, root=central_root)
    shown_payload = _payload(shown, json_loads)
    shown_record = _mapping(_mapping(shown_payload["result"])["worktree"])
    assert shown.returncode == 0, shown.stderr
    assert shown_record["path"] == str(target)
    assert shown_record["managed"] is True
    assert shown_record["removable"] is False
    assert shown_record["remove_blockers"] == ["nested_target_unsupported"]

    remove_args = ["remove", str(target)]
    if force:
        remove_args.append("--force")
    remove_args.extend(["--json"])
    result = cli_runner(*remove_args, repo=temp_git_repo.path, root=central_root)
    payload = _payload(result, json_loads)
    error = _mapping(payload["error"])
    error_details = _mapping(error["details"])

    assert result.returncode == 1
    assert payload["status"] == "error"
    assert error["code"] == "remove_blocked"
    assert error_details["remove_blockers"] == ["nested_target_unsupported"]
    assert target.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "must remain\n"
    assert str(target) in temp_git_repo.git("worktree", "list", "--porcelain").stdout

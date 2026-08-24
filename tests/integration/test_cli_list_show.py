from __future__ import annotations

from pathlib import Path
from typing import Any

from conftest import CliResult, TempGitRepository  # type: ignore[import-not-found]


def _payload(result: CliResult, json_loads: object) -> dict[str, Any]:
    assert callable(json_loads)
    return json_loads(result.stdout)  # type: ignore[operator]


def _add_worktree(repo: TempGitRepository, path: Path, branch: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    repo.git("worktree", "add", "-b", branch, str(path))
    return path


def test_list_returns_main_managed_and_external_records(
    temp_git_repo: TempGitRepository, central_root: Path, tmp_path: Path, cli_runner, json_loads
) -> None:
    managed = _add_worktree(
        temp_git_repo,
        central_root / temp_git_repo.path.name / f"{temp_git_repo.path.name}-managed",
        "managed",
    )
    external = _add_worktree(temp_git_repo, tmp_path / "outside" / "external", "external")

    result = cli_runner("list", "--json", repo=temp_git_repo.path, root=central_root)
    payload = _payload(result, json_loads)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert payload["operation"] == "list"
    records = payload["result"]["worktrees"]
    by_path = {record["path"]: record for record in records}
    assert by_path[str(managed)]["managed"] is True
    assert by_path[str(managed)]["origin"] == "managed_namespace"
    assert by_path[str(external)]["managed"] is False
    assert "outside_managed_namespace" in by_path[str(external)]["remove_blockers"]


def test_show_supports_id_and_absolute_path_selectors(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    target = _add_worktree(
        temp_git_repo,
        central_root / temp_git_repo.path.name / f"{temp_git_repo.path.name}-feature",
        "feature",
    )

    by_id = cli_runner("show", "feature", "--json", repo=temp_git_repo.path, root=central_root)
    by_path = cli_runner("show", str(target), "--json", repo=temp_git_repo.path, root=central_root)
    id_payload = _payload(by_id, json_loads)
    path_payload = _payload(by_path, json_loads)

    assert by_id.returncode == 0, by_id.stderr
    assert by_path.returncode == 0, by_path.stderr
    assert id_payload["operation"] == "show"
    assert id_payload["result"]["worktree"]["path"] == str(target)
    assert path_payload["result"]["worktree"]["id"] == "feature"

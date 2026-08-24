from __future__ import annotations

from pathlib import Path

from conftest import CliResult, TempGitRepository


def _payload(result: CliResult, json_loads: object) -> dict[str, object]:
    assert callable(json_loads)
    return json_loads(result.stdout)  # type: ignore[operator]


def _add_worktree(repo: TempGitRepository, path: Path, branch: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    repo.git("worktree", "add", "-b", branch, str(path))
    return path


def test_list_exposes_managed_and_external_records(
    temp_git_repo: TempGitRepository, central_root: Path, tmp_path: Path, cli_runner, json_loads
) -> None:
    managed = _add_worktree(
        temp_git_repo,
        central_root / temp_git_repo.path.name / f"{temp_git_repo.path.name}-managed",
        "managed",
    )
    external = _add_worktree(temp_git_repo, tmp_path / "external" / "external-record", "external")
    result = cli_runner("list", "--json", repo=temp_git_repo.path, root=central_root)
    payload = _payload(result, json_loads)

    assert result.returncode == 0, result.stderr
    records = payload["result"]["worktrees"]
    by_path = {record["path"]: record for record in records}
    assert by_path[str(managed)]["origin"] == "managed_namespace"
    assert by_path[str(managed)]["managed"] is True
    assert by_path[str(external)]["origin"] == "external"
    assert by_path[str(external)]["managed"] is False
    assert by_path[str(external)]["removable"] is False
    assert "outside_managed_namespace" in by_path[str(external)]["remove_blockers"]


def test_show_prefers_exact_stable_id_before_basename_ambiguity(
    temp_git_repo: TempGitRepository, central_root: Path, tmp_path: Path, cli_runner, json_loads
) -> None:
    first = _add_worktree(temp_git_repo, tmp_path / "left" / "same-name", "left-target")
    _add_worktree(temp_git_repo, tmp_path / "right" / "same-name", "right-target")
    result = cli_runner("show", "same-name", "--json", repo=temp_git_repo.path, root=central_root)
    payload = _payload(result, json_loads)

    assert result.returncode == 0, result.stderr
    assert payload["status"] == "ok"
    assert payload["result"]["worktree"]["id"] == "same-name"
    assert payload["result"]["worktree"]["path"] == str(first)


def test_external_remove_is_blocked_without_git_mutation(
    temp_git_repo: TempGitRepository, central_root: Path, tmp_path: Path, cli_runner, json_loads
) -> None:
    external = _add_worktree(temp_git_repo, tmp_path / "external" / "not-managed", "external-target")
    result = cli_runner("remove", str(external), "--json", repo=temp_git_repo.path, root=central_root)
    payload = _payload(result, json_loads)

    assert result.returncode == 1
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "remove_blocked"
    assert "outside_managed_namespace" in payload["error"]["details"]["remove_blockers"]
    assert external.is_dir()
    assert str(external) in temp_git_repo.git("worktree", "list", "--porcelain").stdout


def test_dirty_remove_requires_explicit_single_force(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    target = _add_worktree(
        temp_git_repo,
        central_root / temp_git_repo.path.name / f"{temp_git_repo.path.name}-dirty",
        "dirty-target",
    )
    (target / "untracked.txt").write_text("dirty\n", encoding="utf-8")

    default_result = cli_runner("remove", str(target), "--json", repo=temp_git_repo.path, root=central_root)
    default_payload = _payload(default_result, json_loads)
    assert default_result.returncode == 1
    assert default_payload["error"]["code"] == "git_worktree_remove_failed"
    assert target.is_dir()

    forced_result = cli_runner("remove", str(target), "--force", "--json", repo=temp_git_repo.path, root=central_root)
    forced_payload = _payload(forced_result, json_loads)
    assert forced_result.returncode == 0, forced_result.stderr
    assert forced_payload["status"] == "ok"
    assert forced_payload["result"]["force_requested"] is True
    assert forced_payload["result"]["branch_deleted"] is False
    assert not target.exists()
    assert "dirty-target" in temp_git_repo.git("branch", "--list").stdout


def test_locked_target_is_blocked_even_with_force(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    target = _add_worktree(
        temp_git_repo,
        central_root / temp_git_repo.path.name / f"{temp_git_repo.path.name}-locked",
        "locked-target",
    )
    temp_git_repo.git("worktree", "lock", "--reason", "P1 contract lock", str(target))
    result = cli_runner("remove", str(target), "--force", "--json", repo=temp_git_repo.path, root=central_root)
    payload = _payload(result, json_loads)

    assert result.returncode == 1
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "remove_blocked"
    assert "locked_worktree" in payload["error"]["details"]["remove_blockers"]
    assert target.is_dir()
    assert str(target) in temp_git_repo.git("worktree", "list", "--porcelain").stdout


def test_namespace_symlink_blocks_create_and_remove(
    temp_git_repo: TempGitRepository,
    tmp_path: Path,
    symlink_capability: Path,
    cli_runner,
    json_loads,
) -> None:
    root = tmp_path / "managed-root"
    root.mkdir()
    namespace = root / temp_git_repo.path.name
    namespace.symlink_to(symlink_capability, target_is_directory=True)

    create_result = cli_runner("create", "symlink-case", "--json", repo=temp_git_repo.path, root=root)
    create_payload = _payload(create_result, json_loads)
    assert create_result.returncode == 1
    assert create_payload["error"]["code"] == "unsafe_namespace"

    remove_result = cli_runner("remove", "anything", "--force", "--json", repo=temp_git_repo.path, root=root)
    remove_payload = _payload(remove_result, json_loads)
    assert remove_result.returncode == 1
    assert remove_payload["error"]["code"] == "unsafe_namespace"
    assert namespace.is_symlink()

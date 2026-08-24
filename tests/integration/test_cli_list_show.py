from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
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


def test_non_utf8_linked_worktree_path_is_valid_json_and_removable(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    namespace = central_root / temp_git_repo.path.name
    invalid_path = Path(
        os.fsdecode(os.fsencode(namespace) + b"/" + temp_git_repo.path.name.encode() + b"-invalid-\xff")
    )
    invalid_path.parent.mkdir(parents=True)
    added = subprocess.run(
        ["git", "worktree", "add", "-b", "invalid-path", str(invalid_path)],
        cwd=temp_git_repo.path,
        capture_output=True,
        text=False,
        check=False,
    )
    if added.returncode != 0:
        pytest.skip(f"Git/filesystem does not support this raw pathname: {os.fsdecode(added.stderr)}")

    listed = cli_runner("list", "--json", repo=temp_git_repo.path, root=central_root)
    listed_payload = _payload(listed, json_loads)
    assert listed.returncode == 0, listed.stderr
    listed_bytes = listed.stdout.encode("utf-8")
    assert listed_bytes
    assert any(record["path"] == str(invalid_path) for record in listed_payload["result"]["worktrees"])

    removed = cli_runner("remove", str(invalid_path), "--json", repo=temp_git_repo.path, root=central_root)
    removed_payload = _payload(removed, json_loads)
    assert removed.returncode == 0, removed.stderr
    assert removed_payload["status"] == "ok"
    assert not invalid_path.exists()


def test_json_wire_is_ascii_safe_under_c_locale_for_unicode_paths(
    git_repo_factory, tmp_path: Path, cli_runner, json_loads
) -> None:
    repo = git_repo_factory(name="repo-日本語")
    root = tmp_path / "managed-日本語"
    environment = {
        "LC_ALL": "C",
        "LANG": "C",
        "PYTHONCOERCECLOCALE": "0",
        "PYTHONUTF8": "0",
        "PYTHONIOENCODING": "ascii",
    }

    created = cli_runner(
        "create", "unicode", "--no-bootstrap", "--json", repo=repo.path, root=root, environment=environment
    )
    created_payload = _payload(created, json_loads)
    assert created.returncode == 0, created.stderr
    assert all(ord(character) < 128 for character in created.stdout)
    target = Path(created_payload["result"]["worktree_path"])
    assert "日本語" in str(target)

    listed = cli_runner("list", "--json", repo=repo.path, root=root, environment=environment)
    listed_payload = _payload(listed, json_loads)
    assert listed.returncode == 0, listed.stderr
    assert all(ord(character) < 128 for character in listed.stdout)
    assert any(record["path"] == str(target) for record in listed_payload["result"]["worktrees"])

    shown = cli_runner("show", "unicode", "--json", repo=repo.path, root=root, environment=environment)
    shown_payload = _payload(shown, json_loads)
    assert shown.returncode == 0, shown.stderr
    assert all(ord(character) < 128 for character in shown.stdout)
    assert shown_payload["result"]["worktree"]["path"] == str(target)

    removed = cli_runner("remove", "unicode", "--json", repo=repo.path, root=root, environment=environment)
    assert removed.returncode == 0, removed.stderr
    assert all(ord(character) < 128 for character in removed.stdout)
    assert not target.exists()


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

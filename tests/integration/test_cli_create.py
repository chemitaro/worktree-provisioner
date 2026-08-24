from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from conftest import TempGitRepository  # type: ignore[import-not-found]

from worktree_provisioner.application.contracts import CreateRequest, ExpectedError, RemoveRequest
from worktree_provisioner.application.ports import ApplicationPorts
from worktree_provisioner.application.worktree_service import WorktreeService
from worktree_provisioner.infra.environment import EnvironmentAdapter
from worktree_provisioner.infra.filesystem import DirectoryHandle, FilesystemCliGateway
from worktree_provisioner.infra.git_cli import GitCliGateway
from worktree_provisioner.infra.make_cli import MakeCliGateway
from worktree_provisioner.presentation import json_v1, text


def test_create_json_contract_has_absolute_facts(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    result = cli_runner("create", "setup", "--no-bootstrap", "--json", repo=temp_git_repo.path, root=central_root)
    payload = json_loads(result.stdout)

    expected = central_root / temp_git_repo.path.name / f"{temp_git_repo.path.name}-setup"
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert payload["schema_version"] == 1
    assert payload["status"] == "ok"
    assert payload["operation"] == "create"
    assert payload["error"] is None
    assert payload["result"]["id"] == "setup"
    assert payload["result"]["branch"].endswith("-setup")
    assert payload["result"]["worktree_path"] == str(expected)
    assert payload["result"]["bootstrap"]["status"] == "disabled"
    assert payload["result"]["artifacts"] == {
        "container_exists": True,
        "worktree_path_exists": True,
        "branch_exists": True,
        "worktree_record_exists": True,
    }


def test_create_retries_real_git_path_race_and_retains_partial_branch(
    temp_git_repo: TempGitRepository, central_root: Path, monkeypatch
) -> None:
    original_add = GitCliGateway.add_worktree_bound
    attempts = 0

    def race_once(
        gateway: GitCliGateway,
        repo_root: Path,
        *,
        directory: DirectoryHandle,
        name: str,
        branch: str,
    ) -> None:
        nonlocal attempts
        attempts += 1
        path = directory.path / name
        if attempts == 1:
            path.mkdir(parents=True)
            (path / "concurrent-file").write_text("race\n", encoding="utf-8")
        original_add(gateway, repo_root, directory=directory, name=name, branch=branch)

    monkeypatch.setattr(GitCliGateway, "add_worktree_bound", race_once)
    git = GitCliGateway()
    result = WorktreeService(
        ApplicationPorts(
            git=git,
            bootstrap=MakeCliGateway(),
            filesystem=FilesystemCliGateway(),
            environment=EnvironmentAdapter(),
        )
    ).create(
        CreateRequest(
            repo_root=temp_git_repo.path,
            root=central_root,
            label=None,
            bootstrap_enabled=False,
        )
    )

    failed_path = central_root / temp_git_repo.path.name / f"{temp_git_repo.path.name}-wt1"
    assert attempts == 2
    assert result.id == "wt2"
    assert failed_path.is_dir()
    assert (failed_path / "concurrent-file").read_text(encoding="utf-8") == "race\n"
    assert git.local_branch_exists(temp_git_repo.path, "main-wt1")
    assert result.worktree_path == central_root / temp_git_repo.path.name / f"{temp_git_repo.path.name}-wt2"
    assert len(result.warnings) == 1
    warning = result.warnings[0]
    assert warning.code == "collision_partial_artifact"
    assert "branch=main-wt1" in warning.message
    assert f"path={failed_path}" in warning.message
    assert "path_exists=True" in warning.message
    assert "branch_exists=True" in warning.message

    document = json_v1.success_document(result)
    assert document["warnings"] == [{"code": warning.code, "message": warning.message, "facts": dict(warning.facts)}]
    text_warnings = text.render_warnings(result)
    assert len(text_warnings) == 1
    assert "worktree-provisioner: warning: code=collision_partial_artifact" in text_warnings[0]
    assert "branch=main-wt1" in text_warnings[0]


def test_create_namespace_swap_to_external_symlink_never_creates_outside(
    temp_git_repo: TempGitRepository, central_root: Path, monkeypatch
) -> None:
    original_add = GitCliGateway.add_worktree_bound
    namespace = central_root / temp_git_repo.path.name
    moved_namespace = central_root / f"{temp_git_repo.path.name}-renamed"
    external = central_root.parent / "external-create-target"
    external.mkdir()
    swapped = False

    def swap_before_git(
        gateway: GitCliGateway,
        repo_root: Path,
        *,
        directory: DirectoryHandle,
        name: str,
        branch: str,
    ) -> None:
        nonlocal swapped
        swapped = True
        namespace.rename(moved_namespace)
        namespace.symlink_to(external, target_is_directory=True)
        original_add(gateway, repo_root, directory=directory, name=name, branch=branch)

    monkeypatch.setattr(GitCliGateway, "add_worktree_bound", swap_before_git)
    service = WorktreeService(
        ApplicationPorts(
            git=GitCliGateway(),
            bootstrap=MakeCliGateway(),
            filesystem=FilesystemCliGateway(),
            environment=EnvironmentAdapter(),
        )
    )

    try:
        result = service.create(
            CreateRequest(
                repo_root=temp_git_repo.path,
                root=central_root,
                label="race",
                bootstrap_enabled=False,
            )
        )
    except ExpectedError:
        result = None

    assert swapped
    assert not (external / f"{temp_git_repo.path.name}-race").exists()
    assert not (external / f"{temp_git_repo.path.name}-wt1").exists()
    assert result is None
    assert (moved_namespace / f"{temp_git_repo.path.name}-race").is_dir()


def test_create_namespace_swap_outside_root_is_rejected_before_git(
    temp_git_repo: TempGitRepository, central_root: Path, monkeypatch
) -> None:
    original_add = GitCliGateway.add_worktree_bound
    namespace = central_root / temp_git_repo.path.name
    moved_namespace = central_root.parent / f"{temp_git_repo.path.name}-outside"
    external = central_root.parent / "external-create-outside"
    external.mkdir()

    def swap_outside(
        gateway: GitCliGateway,
        repo_root: Path,
        *,
        directory: DirectoryHandle,
        name: str,
        branch: str,
    ) -> None:
        namespace.rename(moved_namespace)
        namespace.symlink_to(external, target_is_directory=True)
        original_add(gateway, repo_root, directory=directory, name=name, branch=branch)

    monkeypatch.setattr(GitCliGateway, "add_worktree_bound", swap_outside)
    service = WorktreeService(
        ApplicationPorts(
            git=GitCliGateway(),
            bootstrap=MakeCliGateway(),
            filesystem=FilesystemCliGateway(),
            environment=EnvironmentAdapter(),
        )
    )

    try:
        service.create(
            CreateRequest(
                repo_root=temp_git_repo.path,
                root=central_root,
                label="outside",
                bootstrap_enabled=False,
            )
        )
    except ExpectedError:
        pass
    else:
        raise AssertionError("namespace escape must fail before Git mutation")

    assert not (external / f"{temp_git_repo.path.name}-outside").exists()
    assert not (moved_namespace / f"{temp_git_repo.path.name}-outside").exists()


def test_create_namespace_swap_outside_root_after_bound_check_is_rejected(
    temp_git_repo: TempGitRepository, central_root: Path, monkeypatch
) -> None:
    namespace = central_root / temp_git_repo.path.name
    moved_namespace = central_root.parent / f"{temp_git_repo.path.name}-after-check"
    external = central_root.parent / "external-create-after-check"
    external.mkdir()
    original_check = DirectoryHandle.is_within_bound_root
    swapped = False

    def swap_after_check(handle: DirectoryHandle) -> bool:
        nonlocal swapped
        result = original_check(handle)
        if not swapped:
            swapped = True
            namespace.rename(moved_namespace)
            namespace.symlink_to(external, target_is_directory=True)
        return result

    monkeypatch.setattr(DirectoryHandle, "is_within_bound_root", swap_after_check)
    service = WorktreeService(
        ApplicationPorts(
            git=GitCliGateway(),
            bootstrap=MakeCliGateway(),
            filesystem=FilesystemCliGateway(),
            environment=EnvironmentAdapter(),
        )
    )

    with pytest.raises(ExpectedError) as caught:
        service.create(
            CreateRequest(
                repo_root=temp_git_repo.path,
                root=central_root,
                label="after-check",
                bootstrap_enabled=False,
            )
        )

    assert caught.value.code == "git_worktree_add_failed"
    assert swapped
    assert not (moved_namespace / f"{temp_git_repo.path.name}-after-check").exists()
    assert not (external / f"{temp_git_repo.path.name}-after-check").exists()


def test_remove_namespace_swap_outside_root_is_rejected_before_git(
    temp_git_repo: TempGitRepository, central_root: Path, monkeypatch
) -> None:
    ports = ApplicationPorts(
        git=GitCliGateway(),
        bootstrap=MakeCliGateway(),
        filesystem=FilesystemCliGateway(),
        environment=EnvironmentAdapter(),
    )
    service = WorktreeService(ports)
    created = service.create(
        CreateRequest(
            repo_root=temp_git_repo.path,
            root=central_root,
            label="remove",
            bootstrap_enabled=False,
        )
    )
    namespace = central_root / temp_git_repo.path.name
    moved_namespace = central_root.parent / f"{temp_git_repo.path.name}-remove-outside"
    external = central_root.parent / "external-remove-outside"
    external.mkdir()
    original_remove = GitCliGateway.remove_worktree_bound

    def swap_outside(
        gateway: GitCliGateway,
        repo_root: Path,
        *,
        directory: DirectoryHandle,
        name: str,
        force: bool,
    ) -> None:
        namespace.rename(moved_namespace)
        namespace.symlink_to(external, target_is_directory=True)
        original_remove(gateway, repo_root, directory=directory, name=name, force=force)

    monkeypatch.setattr(GitCliGateway, "remove_worktree_bound", swap_outside)

    with pytest.raises(ExpectedError) as caught:
        service.remove(
            RemoveRequest(
                repo_root=temp_git_repo.path,
                root=central_root,
                target=created.id,
                force=True,
            )
        )

    assert caught.value.code == "git_worktree_remove_failed"
    assert (moved_namespace / f"{temp_git_repo.path.name}-remove").is_dir()
    assert not (external / f"{temp_git_repo.path.name}-remove").exists()


def test_remove_namespace_swap_outside_root_after_bound_check_is_rejected(
    temp_git_repo: TempGitRepository, central_root: Path, monkeypatch
) -> None:
    ports = ApplicationPorts(
        git=GitCliGateway(),
        bootstrap=MakeCliGateway(),
        filesystem=FilesystemCliGateway(),
        environment=EnvironmentAdapter(),
    )
    service = WorktreeService(ports)
    created = service.create(
        CreateRequest(
            repo_root=temp_git_repo.path,
            root=central_root,
            label="remove-after-check",
            bootstrap_enabled=False,
        )
    )
    namespace = central_root / temp_git_repo.path.name
    moved_namespace = central_root.parent / f"{temp_git_repo.path.name}-remove-after-check"
    external = central_root.parent / "external-remove-after-check"
    external.mkdir()
    original_check = DirectoryHandle.is_within_bound_root
    swapped = False

    def swap_after_check(handle: DirectoryHandle) -> bool:
        nonlocal swapped
        result = original_check(handle)
        if not swapped:
            swapped = True
            namespace.rename(moved_namespace)
            namespace.symlink_to(external, target_is_directory=True)
        return result

    monkeypatch.setattr(DirectoryHandle, "is_within_bound_root", swap_after_check)

    with pytest.raises(ExpectedError) as caught:
        service.remove(
            RemoveRequest(
                repo_root=temp_git_repo.path,
                root=central_root,
                target=created.id,
                force=True,
            )
        )

    assert caught.value.code == "git_worktree_remove_failed"
    assert swapped
    assert (moved_namespace / f"{temp_git_repo.path.name}-remove-after-check").is_dir()
    assert not (external / f"{temp_git_repo.path.name}-remove-after-check").exists()


def test_create_without_label_text_contract_uses_wt1_and_absolute_path(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner
) -> None:
    result = cli_runner("create", "--no-bootstrap", repo=temp_git_repo.path, root=central_root)
    expected = central_root / temp_git_repo.path.name / f"{temp_git_repo.path.name}-wt1"

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert "worktree-provisioner: ok (create)" in result.stdout
    assert "id=wt1" in result.stdout
    assert "branch=main-wt1" in result.stdout
    assert f"path={expected}" in result.stdout
    assert "bootstrap status=disabled" in result.stdout
    assert expected.is_dir()


def test_create_from_linked_checkout_uses_main_namespace_and_linked_branch_prefix(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    first = cli_runner("create", "outer", "--no-bootstrap", "--json", repo=temp_git_repo.path, root=central_root)
    assert first.returncode == 0, first.stderr
    linked = central_root / temp_git_repo.path.name / f"{temp_git_repo.path.name}-outer"
    linked_branch = temp_git_repo.git("-C", str(linked), "branch", "--show-current").stdout.strip()

    second = cli_runner("create", "inner", "--no-bootstrap", "--json", repo=linked, root=central_root)
    payload = json_loads(second.stdout)
    expected_namespace = central_root / temp_git_repo.path.name
    expected_path = expected_namespace / f"{temp_git_repo.path.name}-inner"

    assert second.returncode == 0, second.stderr
    assert payload["result"]["container_path"] == str(expected_namespace)
    assert payload["result"]["worktree_path"] == str(expected_path)
    assert payload["result"]["branch"] == f"{linked_branch}-inner"
    assert expected_namespace.name == temp_git_repo.path.name
    assert expected_path.parent == expected_namespace
    assert expected_path.is_dir()


def test_create_from_diverged_linked_checkout_uses_linked_head_as_start_point(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner
) -> None:
    linked = central_root / "invocation-linked"
    temp_git_repo.git("worktree", "add", "-b", "linked", str(linked))
    (linked / "linked-only.txt").write_text("linked\n", encoding="utf-8")
    temp_git_repo.git("-C", str(linked), "add", "linked-only.txt")
    temp_git_repo.git("-C", str(linked), "commit", "-m", "linked head")
    linked_head = temp_git_repo.git("-C", str(linked), "rev-parse", "HEAD").stdout.strip()

    result = cli_runner("create", "child", "--no-bootstrap", "--json", repo=linked, root=central_root)
    assert result.returncode == 0, result.stderr
    created_path = central_root / temp_git_repo.path.name / f"{temp_git_repo.path.name}-child"
    created_head = temp_git_repo.git("rev-parse", "refs/heads/linked-child").stdout.strip()

    assert created_head == linked_head
    assert (created_path / "linked-only.txt").read_text(encoding="utf-8") == "linked\n"


def test_create_without_label_skips_directory_only_collision(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    namespace = central_root / temp_git_repo.path.name
    (namespace / f"{temp_git_repo.path.name}-wt1").mkdir(parents=True)

    result = cli_runner("create", "--no-bootstrap", "--json", repo=temp_git_repo.path, root=central_root)
    payload = json_loads(result.stdout)

    assert result.returncode == 0
    assert payload["result"]["id"] == "wt2"


def test_create_with_label_skips_branch_only_collision(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    temp_git_repo.git("branch", "main-feature")

    result = cli_runner("create", "feature", "--no-bootstrap", "--json", repo=temp_git_repo.path, root=central_root)
    payload = json_loads(result.stdout)

    assert result.returncode == 0
    assert payload["result"]["id"] == "feature2"
    assert payload["result"]["branch"] == "main-feature2"


def test_create_with_label_skips_record_only_collision(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    namespace = central_root / temp_git_repo.path.name
    stale_path = namespace / f"{temp_git_repo.path.name}-record"
    stale_path.parent.mkdir(parents=True)
    temp_git_repo.git("worktree", "add", "-b", "main-record", str(stale_path))
    shutil.rmtree(stale_path)

    result = cli_runner("create", "record", "--no-bootstrap", "--json", repo=temp_git_repo.path, root=central_root)
    payload = json_loads(result.stdout)

    assert result.returncode == 0
    assert payload["result"]["id"] == "record2"
    assert (namespace / f"{temp_git_repo.path.name}-record2").is_dir()


def test_create_partial_keeps_worktree_and_returns_nonzero(
    git_repo_factory, central_root: Path, cli_runner, json_loads
) -> None:
    repo = git_repo_factory(makefile="init:\n\t@exit 7\n")
    result = cli_runner("create", "setup", "--json", repo=repo.path, root=central_root)
    payload = json_loads(result.stdout)
    expected = central_root / repo.path.name / f"{repo.path.name}-setup"

    assert result.returncode == 1
    assert result.stderr == ""
    assert payload["status"] == "partial"
    assert payload["error"]["code"] == "bootstrap_failed"
    assert payload["result"]["worktree_path"] == str(expected)
    assert payload["result"]["bootstrap"]["status"] == "failed"
    assert payload["result"]["artifacts"]["worktree_path_exists"] is True
    assert expected.is_dir()


def test_create_without_makefile_reports_skipped_success(
    git_repo_factory, central_root: Path, cli_runner, json_loads
) -> None:
    repo = git_repo_factory()
    result = cli_runner("create", "default", "--json", repo=repo.path, root=central_root)
    payload = json_loads(result.stdout)

    assert result.returncode == 0
    assert payload["result"]["bootstrap"]["status"] == "skipped"
    assert payload["result"]["bootstrap"]["command"] is None


def test_create_with_makefile_without_init_reports_skipped_success(
    git_repo_factory, central_root: Path, cli_runner, json_loads
) -> None:
    repo = git_repo_factory(makefile="all:\n\t@echo all\n")
    result = cli_runner("create", "no-init", "--json", repo=repo.path, root=central_root)
    payload = json_loads(result.stdout)

    assert result.returncode == 0
    assert payload["result"]["bootstrap"]["status"] == "skipped"
    assert payload["result"]["bootstrap"]["command"] is None


def test_create_with_successful_make_init_reports_succeeded(
    git_repo_factory, central_root: Path, cli_runner, json_loads
) -> None:
    repo = git_repo_factory(makefile="init:\n\t@printf initialized > .init-ran\n")
    result = cli_runner("create", "success", "--json", repo=repo.path, root=central_root)
    payload = json_loads(result.stdout)
    target = central_root / repo.path.name / f"{repo.path.name}-success"

    assert result.returncode == 0
    assert payload["result"]["bootstrap"]["status"] == "succeeded"
    assert payload["result"]["bootstrap"]["command"] == ["make", "init"]
    assert (target / ".init-ran").read_text(encoding="utf-8") == "initialized"


def test_create_rejects_broken_root_symlink_without_creating_target(
    temp_git_repo: TempGitRepository, tmp_path: Path, cli_runner, json_loads
) -> None:
    root = tmp_path / "root-link"
    missing_target = tmp_path / "not-created"
    root.symlink_to(missing_target)
    result = cli_runner("create", "unsafe", "--json", repo=temp_git_repo.path, root=root)
    payload = json_loads(result.stdout)

    assert result.returncode == 1
    assert payload["error"]["code"] == "invalid_root"
    assert root.is_symlink()
    assert not missing_target.exists()


def test_create_retries_label_after_directory_and_branch_collisions(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    namespace = central_root / temp_git_repo.path.name
    namespace.mkdir(parents=True)
    (namespace / f"{temp_git_repo.path.name}-feature").mkdir()
    temp_git_repo.git("branch", "main-feature2")

    result = cli_runner("create", "feature", "--no-bootstrap", "--json", repo=temp_git_repo.path, root=central_root)
    payload = json_loads(result.stdout)

    assert result.returncode == 0
    assert payload["result"]["id"] == "feature3"
    assert (namespace / f"{temp_git_repo.path.name}-feature3").is_dir()


def test_create_uses_environment_root_when_explicit_root_is_absent(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    result = cli_runner(
        "create",
        "--no-bootstrap",
        "--json",
        repo=temp_git_repo.path,
        environment={"WORKTREE_PROVISIONER_ROOT": str(central_root)},
    )
    payload = json_loads(result.stdout)

    assert result.returncode == 0
    assert payload["result"]["worktree_path"] == str(
        central_root / temp_git_repo.path.name / f"{temp_git_repo.path.name}-wt1"
    )


def test_explicit_invalid_root_does_not_fallback_to_valid_environment(
    temp_git_repo: TempGitRepository, central_root: Path, tmp_path: Path, cli_runner, json_loads
) -> None:
    invalid_root = tmp_path / "not-a-directory"
    invalid_root.write_text("file", encoding="utf-8")
    result = cli_runner(
        "create",
        "--no-bootstrap",
        "--json",
        repo=temp_git_repo.path,
        root=invalid_root,
        environment={"WORKTREE_PROVISIONER_ROOT": str(central_root)},
    )
    payload = json_loads(result.stdout)

    assert result.returncode == 1
    assert payload["error"]["code"] == "invalid_root"
    assert not (central_root / temp_git_repo.path.name).exists()
    assert not temp_git_repo.git("branch", "--list", "main-wt1").stdout.strip()


def test_blank_explicit_root_does_not_fallback_to_environment(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    result = cli_runner(
        "create",
        "--no-bootstrap",
        "--json",
        repo=temp_git_repo.path,
        root="",
        environment={"WORKTREE_PROVISIONER_ROOT": str(central_root)},
    )
    payload = json_loads(result.stdout)

    assert result.returncode == 1
    assert payload["error"]["code"] == "root_required"
    assert not central_root.exists()


def test_relative_root_is_rejected_before_git_add(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    result = cli_runner(
        "create",
        "--no-bootstrap",
        "--json",
        repo=temp_git_repo.path,
        root="relative/worktrees",
        environment={"WORKTREE_PROVISIONER_ROOT": str(central_root)},
    )
    payload = json_loads(result.stdout)

    assert result.returncode == 1
    assert payload["error"]["code"] == "invalid_root"
    assert not central_root.exists()


def test_regular_file_root_is_rejected_without_mutation(
    temp_git_repo: TempGitRepository, tmp_path: Path, cli_runner, json_loads
) -> None:
    root = tmp_path / "root-file"
    root.write_text("not a directory", encoding="utf-8")
    result = cli_runner("create", "file-root", "--no-bootstrap", "--json", repo=temp_git_repo.path, root=root)
    payload = json_loads(result.stdout)

    assert result.returncode == 1
    assert payload["error"]["code"] == "invalid_root"
    assert root.is_file()
    assert not temp_git_repo.git("branch", "--list", "main-file-root").stdout.strip()


def test_missing_root_is_created_for_create(
    temp_git_repo: TempGitRepository, tmp_path: Path, cli_runner, json_loads
) -> None:
    root = tmp_path / "new" / "root"
    result = cli_runner("create", "--no-bootstrap", "--json", repo=temp_git_repo.path, root=root)
    payload = json_loads(result.stdout)

    assert result.returncode == 0
    assert root.is_dir()
    assert payload["result"]["container_path"] == str(root / temp_git_repo.path.name)


def test_tilde_root_is_expanded(temp_git_repo: TempGitRepository, tmp_path: Path, cli_runner, json_loads) -> None:
    result = cli_runner(
        "create",
        "--no-bootstrap",
        "--json",
        repo=temp_git_repo.path,
        root="~/tilde-root",
        environment={"HOME": str(tmp_path)},
    )
    payload = json_loads(result.stdout)

    expected = tmp_path / "tilde-root" / temp_git_repo.path.name
    assert result.returncode == 0
    assert payload["result"]["container_path"] == str(expected)


def test_directory_symlink_root_is_allowed_and_result_is_canonical(
    temp_git_repo: TempGitRepository, tmp_path: Path, cli_runner, json_loads
) -> None:
    target = tmp_path / "real-root"
    target.mkdir()
    root_link = tmp_path / "root-link"
    root_link.symlink_to(target, target_is_directory=True)
    result = cli_runner("create", "--no-bootstrap", "--json", repo=temp_git_repo.path, root=root_link)
    payload = json_loads(result.stdout)

    assert result.returncode == 0
    assert payload["result"]["container_path"] == str(target / temp_git_repo.path.name)
    assert (target / temp_git_repo.path.name).is_dir()


def test_root_mkdir_failure_is_expected_and_does_not_call_git_add(
    temp_git_repo: TempGitRepository, tmp_path: Path, cli_runner, json_loads
) -> None:
    parent_file = tmp_path / "parent-file"
    parent_file.write_text("cannot contain a directory", encoding="utf-8")
    root = parent_file / "child"
    result = cli_runner("create", "mkdir-failure", "--json", repo=temp_git_repo.path, root=root)
    payload = json_loads(result.stdout)

    assert result.returncode == 1
    assert payload["error"]["code"] == "invalid_root"
    assert not temp_git_repo.git("branch", "--list", "main-mkdir-failure").stdout.strip()


def test_legacy_root_only_is_rejected(temp_git_repo: TempGitRepository, tmp_path: Path, cli_runner, json_loads) -> None:
    legacy = tmp_path / "legacy"
    result = cli_runner(
        "create",
        "legacy",
        "--json",
        repo=temp_git_repo.path,
        environment={"SPEC_DOCK_WORKTREE_ROOT": str(legacy)},
    )
    payload = json_loads(result.stdout)

    assert result.returncode == 1
    assert payload["error"]["code"] == "root_required"
    assert not legacy.exists()

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

import pytest
from conftest import FakeBootstrapGateway, FakeGitGateway, FakeGitRecord  # type: ignore[import-not-found]

from worktree_provisioner.application.contracts import (
    ExpectedError,
    ListRequest,
    RemoveRequest,
    RemoveResult,
)
from worktree_provisioner.application.ports import ApplicationPorts, DirectoryCapability
from worktree_provisioner.application.worktree_service import WorktreeService
from worktree_provisioner.infra.environment import EnvironmentAdapter
from worktree_provisioner.infra.filesystem import DirectoryHandle, FilesystemCliGateway
from worktree_provisioner.presentation.json_v2 import error_document


def _service(git: FakeGitGateway, filesystem: FilesystemCliGateway | None = None) -> WorktreeService:
    return WorktreeService(
        ApplicationPorts(
            git=git,  # type: ignore[arg-type]
            bootstrap=FakeBootstrapGateway(),  # type: ignore[arg-type]
            filesystem=filesystem or FilesystemCliGateway(),
            environment=EnvironmentAdapter(),
        )
    )


def _fixture(
    tmp_path: Path, *, locked: bool = False, external: bool = False
) -> tuple[Path, Path, Path, FakeGitGateway]:
    repo = tmp_path / "checkout"
    repo.mkdir()
    root = tmp_path / "root"
    namespace = root / repo.name
    namespace.mkdir(parents=True)
    target_parent = tmp_path / "external" if external else namespace
    target = target_parent / f"{repo.name}-feature"
    target.mkdir(parents=True)
    git = FakeGitGateway(
        checkout_root=repo,
        records=[
            FakeGitRecord(path=repo, branch="main"),
            FakeGitRecord(path=target, branch="feature", locked=locked),
        ],
        branches={"main", "feature"},
    )
    return repo, root, target, git


def _request(repo: Path, root: Path, target: Path | str, *, force: bool = False) -> RemoveRequest:
    return RemoveRequest(repo_root=repo, root=root, target=str(target), force=force)


def _blockers(error: ExpectedError) -> tuple[str, ...]:
    return tuple(cast(list[str], error.details["remove_blockers"]))


class _SnapshotGit(FakeGitGateway):
    snapshots: tuple[list[FakeGitRecord], list[FakeGitRecord]]
    list_calls = 0

    def worktree_list(self, repo_root: Path) -> list[FakeGitRecord]:
        self._record("worktree_list", repo_root)
        snapshot = self.snapshots[min(self.list_calls, len(self.snapshots) - 1)]
        self.list_calls += 1
        return list(snapshot)


class _IdentityFilesystem(FilesystemCliGateway):
    def path_identity_no_follow(self, path: Path) -> tuple[int, int] | None:
        try:
            info = path.lstat()
        except OSError:
            return None
        return (info.st_dev, info.st_ino)


def test_clean_managed_remove_is_git_first_and_keeps_branch(tmp_path: Path) -> None:
    repo, root, target, git = _fixture(tmp_path)

    result = _service(git).remove(_request(repo, root, target))

    assert result.removed_record is True
    assert result.removed_directory is True
    assert result.branch_deleted is False
    assert target.exists() is False
    assert "feature" in git.branches
    remove_calls = [call for call in git.calls if call[0] == "remove_worktree"]
    assert len(remove_calls) == 1
    assert remove_calls[0][2]["force"] is False


@pytest.mark.parametrize("force", [False, True])
def test_locked_target_is_blocked_before_git_even_with_force(tmp_path: Path, force: bool) -> None:
    repo, root, target, git = _fixture(tmp_path, locked=True)

    with pytest.raises(ExpectedError) as caught:
        _service(git).remove(_request(repo, root, target, force=force))

    assert caught.value.code == "remove_blocked"
    assert "locked_worktree" in _blockers(caught.value)
    assert not [call for call in git.calls if call[0] == "remove_worktree"]
    assert target.is_dir()


@pytest.mark.parametrize("force", [False, True])
def test_external_target_is_blocked_before_git_even_with_force(tmp_path: Path, force: bool) -> None:
    repo, root, target, git = _fixture(tmp_path, external=True)

    with pytest.raises(ExpectedError) as caught:
        _service(git).remove(_request(repo, root, target, force=force))

    assert caught.value.code == "remove_blocked"
    assert "outside_managed_namespace" in _blockers(caught.value)
    assert not [call for call in git.calls if call[0] == "remove_worktree"]
    assert target.is_dir()


@pytest.mark.parametrize("force", [False, True])
def test_nested_managed_target_is_blocked_before_git_even_with_force(tmp_path: Path, force: bool) -> None:
    repo, root, target, git = _fixture(tmp_path)
    nested = root / repo.name / "nested" / target.name
    nested.parent.mkdir()
    target.rename(nested)
    git.records[1] = FakeGitRecord(path=nested, branch="feature")

    class TrackingFilesystem(FilesystemCliGateway):
        cleanup_calls = 0

        def remove_target_no_follow(self, path: Path) -> None:
            self.cleanup_calls += 1
            super().remove_target_no_follow(path)

        def remove_target_no_follow_bound(self, directory: DirectoryCapability, path: Path) -> None:
            self.cleanup_calls += 1
            super().remove_target_no_follow_bound(directory, path)

    filesystem = TrackingFilesystem()

    with pytest.raises(ExpectedError) as caught:
        _service(git, filesystem).remove(_request(repo, root, nested, force=force))

    assert caught.value.code == "remove_blocked"
    assert "nested_target_unsupported" in _blockers(caught.value)
    assert not [call for call in git.calls if call[0] == "remove_worktree"]
    assert filesystem.cleanup_calls == 0
    assert nested.is_dir()


@pytest.mark.parametrize(
    ("case", "force"),
    [
        ("main", False),
        ("main", True),
        ("current", False),
        ("current", True),
        ("bare", False),
        ("bare", True),
        ("path_missing", False),
        ("path_missing", True),
        ("root", False),
        ("root", True),
        ("namespace", False),
        ("namespace", True),
        ("protected_ancestor", False),
        ("protected_ancestor", True),
    ],
)
def test_hard_blockers_never_reach_git_remove(tmp_path: Path, case: str, force: bool) -> None:
    repo, root, target, git = _fixture(tmp_path)
    selector: str = str(target)
    expected_blocker: str
    if case == "main":
        selector = str(repo)
        expected_blocker = "main_worktree"
    elif case == "current":
        git.records.append(FakeGitRecord(path=repo, branch="current-target"))
        selector = _service(git).list(ListRequest(repo_root=repo, root=root)).worktrees[2].id
        expected_blocker = "current_worktree"
    elif case == "bare":
        git.records[1] = FakeGitRecord(path=target, branch="feature", bare=True)
        expected_blocker = "bare_worktree"
    elif case == "path_missing":
        target.rmdir()
        expected_blocker = "path_missing"
    elif case == "root":
        git.records.append(FakeGitRecord(path=root, branch="root-target"))
        selector = str(root)
        expected_blocker = "protected_cleanup_path"
    elif case == "namespace":
        namespace = root / repo.name
        git.records.append(FakeGitRecord(path=namespace, branch="namespace-target"))
        selector = str(namespace)
        expected_blocker = "protected_cleanup_path"
    else:
        ancestor = root.parent
        git.records.append(FakeGitRecord(path=ancestor, branch="ancestor-target"))
        selector = str(ancestor)
        expected_blocker = "protected_cleanup_path"

    with pytest.raises(ExpectedError) as caught:
        _service(git).remove(_request(repo, root, selector, force=force))

    assert caught.value.code == "remove_blocked"
    assert expected_blocker in _blockers(caught.value)
    assert not [call for call in git.calls if call[0] == "remove_worktree"]


@pytest.mark.parametrize("target_kind", ["main", "main_ancestor"])
@pytest.mark.parametrize("force", [False, True])
def test_linked_checkout_protects_main_inventory_path_and_ancestors(
    tmp_path: Path, target_kind: str, force: bool
) -> None:
    root = tmp_path / "root"
    namespace = root / "checkout"
    main_ancestor = namespace / "nested"
    main = main_ancestor / "checkout"
    main.mkdir(parents=True)
    linked = tmp_path / "linked-checkout"
    linked.mkdir()
    records = [
        FakeGitRecord(path=main, branch="main"),
        FakeGitRecord(path=main_ancestor, branch="ancestor"),
    ]
    git = FakeGitGateway(checkout_root=linked, records=records, branches={"main", "ancestor"})
    target = main if target_kind == "main" else main_ancestor

    with pytest.raises(ExpectedError) as caught:
        _service(git).remove(_request(linked, root, target, force=force))

    assert caught.value.code == "remove_blocked"
    assert "protected_cleanup_path" in _blockers(caught.value)
    assert not [call for call in git.calls if call[0] == "remove_worktree"]


def test_namespace_symlink_is_classified_as_unsafe_for_remove(tmp_path: Path) -> None:
    repo, root, target, git = _fixture(tmp_path)
    target.rmdir()
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    namespace = root / repo.name
    namespace.rmdir()
    namespace.symlink_to(redirected, target_is_directory=True)

    with pytest.raises(ExpectedError) as caught:
        _service(git).remove(_request(repo, root, target, force=True))

    assert caught.value.code == "unsafe_namespace"
    assert not [call for call in git.calls if call[0] == "remove_worktree"]


def test_git_failure_does_not_call_filesystem_cleanup(tmp_path: Path) -> None:
    repo, root, target, git = _fixture(tmp_path)
    git.remove_error = RuntimeError("dirty worktree")

    with pytest.raises(ExpectedError) as caught:
        _service(git).remove(_request(repo, root, target))

    assert caught.value.code == "git_worktree_remove_failed"
    assert caught.value.status == "error"
    assert caught.value.result is None
    assert target.is_dir()


def test_git_failure_after_record_removal_reports_partial_without_cleanup(tmp_path: Path) -> None:
    repo, root, target, base_git = _fixture(tmp_path)
    state = {"removed": False}

    class RecordRemovedThenFails(FakeGitGateway):
        def remove_worktree(self, repo_root: Path, *, path: Path, force: bool) -> None:
            self._record("remove_worktree", repo_root, path=path, force=force)
            self.records = [record for record in self.records if record.path != path]
            state["removed"] = True
            raise RuntimeError("Git removed the record but could not remove the directory")

    class TrackingFilesystem(FilesystemCliGateway):
        cleanup_calls = 0

        def remove_target_no_follow(self, path: Path) -> None:
            self.cleanup_calls += 1
            super().remove_target_no_follow(path)

        def remove_target_no_follow_bound(self, directory: DirectoryCapability, path: Path) -> None:
            self.cleanup_calls += 1
            super().remove_target_no_follow_bound(directory, path)

    git = RecordRemovedThenFails(
        checkout_root=base_git.checkout_root,
        records=list(base_git.records),
        branches=set(base_git.branches),
    )
    filesystem = TrackingFilesystem()

    with pytest.raises(ExpectedError) as caught:
        _service(git, filesystem).remove(_request(repo, root, target))

    assert state["removed"]
    assert caught.value.code == "git_worktree_remove_partial"
    assert caught.value.status == "partial"
    result = cast(RemoveResult, caught.value.result)
    assert result.removed_record is True
    assert result.removed_directory is False
    assert result.branch_deleted is False
    assert target.is_dir()
    assert filesystem.cleanup_calls == 0
    assert len([call for call in git.calls if call[0] == "worktree_list"]) == 3
    document = error_document(caught.value)
    assert document["status"] == "partial"
    assert document["operation"] == "remove"
    envelope_result = cast(dict[str, object], document["result"])
    assert envelope_result["removed_record"] is True
    assert envelope_result["removed_directory"] is False
    envelope_error = cast(dict[str, object], document["error"])
    assert envelope_error["code"] == "git_worktree_remove_partial"


def test_remove_failure_observation_error_keeps_primary_git_diagnostic(tmp_path: Path) -> None:
    repo, root, target, base_git = _fixture(tmp_path)
    state = {"removed": False}

    class RemovingGit(FakeGitGateway):
        def remove_worktree(self, repo_root: Path, *, path: Path, force: bool) -> None:
            self._record("remove_worktree", repo_root, path=path, force=force)
            state["removed"] = True
            raise RuntimeError("primary Git refusal")

    class FailingObservationFilesystem(FilesystemCliGateway):
        def lstat_kind(self, path: Path) -> str:
            if path == target and state["removed"]:
                raise PermissionError("post-failure inspection denied")
            return super().lstat_kind(path)

        def remove_target_no_follow_bound(self, directory: DirectoryCapability, path: Path) -> None:
            raise AssertionError("cleanup must not run after Git failure")

    git = RemovingGit(
        checkout_root=base_git.checkout_root,
        records=list(base_git.records),
        branches=set(base_git.branches),
    )

    with pytest.raises(ExpectedError) as caught:
        _service(git, FailingObservationFilesystem()).remove(_request(repo, root, target))

    assert caught.value.code == "git_worktree_remove_failed"
    assert caught.value.status == "error"
    assert caught.value.result is None
    assert caught.value.details["diagnostic"] == "primary Git refusal"
    observation = cast(dict[str, object], caught.value.details["observation"])
    assert observation["target_lstat_error"] == "post-failure inspection denied"
    assert target.is_dir()


def test_namespace_symlink_is_rejected_before_remove(tmp_path: Path) -> None:
    repo, root, target, git = _fixture(tmp_path)
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    namespace = root / repo.name
    target.rmdir()
    namespace.rmdir()
    namespace.symlink_to(redirected, target_is_directory=True)

    with pytest.raises(ExpectedError) as caught:
        _service(git).remove(_request(repo, root, target, force=True))

    assert caught.value.code == "unsafe_namespace"
    assert not [call for call in git.calls if call[0] == "remove_worktree"]


def test_final_refresh_missing_record_is_fail_closed(tmp_path: Path) -> None:
    repo, root, target, git = _fixture(tmp_path)

    class DisappearingGit(FakeGitGateway):
        snapshots: list[list[FakeGitRecord]]
        list_calls: int = 0

        def worktree_list(self, repo_root: Path) -> list[FakeGitRecord]:
            self._record("worktree_list", repo_root)
            snapshot = self.snapshots[min(self.list_calls, len(self.snapshots) - 1)]
            self.list_calls += 1
            return list(snapshot)

    disappearing = DisappearingGit(checkout_root=repo, records=git.records)
    disappearing.snapshots = [list(git.records), [git.records[0]]]

    with pytest.raises(ExpectedError) as caught:
        _service(disappearing).remove(_request(repo, root, target))

    assert caught.value.code == "remove_blocked"
    assert "record_missing_after_refresh" in _blockers(caught.value)
    assert not [call for call in disappearing.calls if call[0] == "remove_worktree"]
    assert target.is_dir()


def test_final_refresh_path_change_is_fail_closed(tmp_path: Path) -> None:
    repo, root, _target, git = _fixture(tmp_path)
    replacement = root / repo.name / "nested" / f"{repo.name}-feature"
    replacement.parent.mkdir(parents=True, exist_ok=True)

    class RepointingGit(FakeGitGateway):
        snapshots: list[list[FakeGitRecord]]
        list_calls: int = 0

        def worktree_list(self, repo_root: Path) -> list[FakeGitRecord]:
            self._record("worktree_list", repo_root)
            snapshot = self.snapshots[min(self.list_calls, len(self.snapshots) - 1)]
            self.list_calls += 1
            return list(snapshot)

    repointed = RepointingGit(checkout_root=repo, records=git.records)
    repointed.snapshots = [
        list(git.records),
        [git.records[0], FakeGitRecord(path=replacement, branch="feature")],
    ]

    with pytest.raises(ExpectedError) as caught:
        _service(repointed).remove(_request(repo, root, "feature"))

    assert caught.value.code == "remove_blocked"
    assert "target_changed_after_refresh" in _blockers(caught.value)
    assert not [call for call in repointed.calls if call[0] == "remove_worktree"]


def test_final_refresh_new_basename_ambiguity_is_fail_closed(tmp_path: Path) -> None:
    repo, root, target, git = _fixture(tmp_path)
    other = tmp_path / "other" / target.name
    other.parent.mkdir(parents=True)
    other.mkdir()

    class AmbiguousGit(FakeGitGateway):
        snapshots: list[list[FakeGitRecord]]
        list_calls: int = 0

        def worktree_list(self, repo_root: Path) -> list[FakeGitRecord]:
            self._record("worktree_list", repo_root)
            snapshot = self.snapshots[min(self.list_calls, len(self.snapshots) - 1)]
            self.list_calls += 1
            return list(snapshot)

    ambiguous = AmbiguousGit(checkout_root=repo, records=git.records)
    ambiguous.snapshots = [
        list(git.records),
        [*git.records, FakeGitRecord(path=other, branch="other")],
    ]

    with pytest.raises(ExpectedError) as caught:
        _service(ambiguous).remove(_request(repo, root, target.name))

    assert caught.value.code == "remove_blocked"
    assert "target_changed_after_refresh" in _blockers(caught.value)
    assert not [call for call in ambiguous.calls if call[0] == "remove_worktree"]


@pytest.mark.parametrize(
    ("blocker_kind", "expected_blocker"),
    [
        ("bare", "bare_worktree"),
        ("locked", "locked_worktree"),
    ],
)
def test_final_refresh_new_blocker_is_fail_closed(tmp_path: Path, blocker_kind: str, expected_blocker: str) -> None:
    repo, root, target, git = _fixture(tmp_path)

    class BlockedRefreshGit(FakeGitGateway):
        snapshots: list[list[FakeGitRecord]]
        list_calls: int = 0

        def worktree_list(self, repo_root: Path) -> list[FakeGitRecord]:
            self._record("worktree_list", repo_root)
            snapshot = self.snapshots[min(self.list_calls, len(self.snapshots) - 1)]
            self.list_calls += 1
            return list(snapshot)

    if blocker_kind == "bare":
        refreshed_record = FakeGitRecord(path=target, branch="feature", bare=True)
    else:
        refreshed_record = FakeGitRecord(path=target, branch="feature", locked=True, lock_reason="refresh lock")
    blocked = BlockedRefreshGit(checkout_root=repo, records=git.records)
    blocked.snapshots = [list(git.records), [git.records[0], refreshed_record]]

    with pytest.raises(ExpectedError) as caught:
        _service(blocked).remove(_request(repo, root, target))

    assert caught.value.code == "remove_blocked"
    assert expected_blocker in _blockers(caught.value)
    assert not [call for call in blocked.calls if call[0] == "remove_worktree"]


def test_namespace_that_becomes_symlink_is_rejected_before_git(tmp_path: Path) -> None:
    repo, root, target, base_git = _fixture(tmp_path)
    namespace = root / repo.name
    redirected = tmp_path / "redirected"
    redirected.mkdir()
    target.rmdir()

    def flip_namespace() -> None:
        namespace.rmdir()
        namespace.symlink_to(redirected, target_is_directory=True)

    class NamespaceRaceGit(FakeGitGateway):
        def worktree_list(self, repo_root: Path) -> list[FakeGitRecord]:
            records = super().worktree_list(repo_root)
            # The namespace changes after the initial Git snapshot and before
            # the remove preflight validates it.
            flip_namespace()
            return records

    git = NamespaceRaceGit(
        checkout_root=base_git.checkout_root,
        records=list(base_git.records),
        branches=set(base_git.branches),
    )

    with pytest.raises(ExpectedError) as caught:
        _service(git).remove(_request(repo, root, target, force=True))

    assert caught.value.code == "unsafe_namespace"
    assert caught.value.details["reason"] == "symlink"
    assert namespace.is_symlink()
    assert not [call for call in git.calls if call[0] == "remove_worktree"]


def test_post_git_lstat_race_is_reported_as_cleanup_partial(tmp_path: Path) -> None:
    repo, root, target, base_git = _fixture(tmp_path)
    state = {"git_removed": False}

    class LstatRaceFilesystem(FilesystemCliGateway):
        def lstat_kind(self, path: Path) -> str:
            if path == target and state["git_removed"]:
                raise PermissionError("target changed during cleanup")
            return FilesystemCliGateway.lstat_kind(self, path)

    filesystem = LstatRaceFilesystem()

    class RemovingGit(FakeGitGateway):
        def remove_worktree(self, repo_root: Path, *, path: Path, force: bool) -> None:
            state["git_removed"] = True
            FakeGitGateway.remove_worktree(self, repo_root, path=path, force=force)

    git = RemovingGit(
        checkout_root=base_git.checkout_root,
        records=base_git.records,
        branches=base_git.branches,
    )

    with pytest.raises(ExpectedError) as caught:
        _service(git, filesystem).remove(_request(repo, root, target, force=True))

    assert caught.value.code == "post_remove_cleanup_failed"
    result = cast(RemoveResult, caught.value.result)
    assert result.removed_record is True
    assert result.removed_directory is False


def test_cleanup_namespace_swap_to_external_symlink_never_removes_outside(
    tmp_path: Path,
) -> None:
    repo, root, target, git = _fixture(tmp_path)
    namespace = root / repo.name
    moved_namespace = root / f"{repo.name}-renamed"
    external = tmp_path / "external-cleanup"
    external.mkdir()
    state = {"git_removed": False, "swapped": False}

    class SwapAfterTargetCheckFilesystem(FilesystemCliGateway):
        def path_exists_no_follow(self, path: Path) -> bool:
            exists = FilesystemCliGateway.path_exists_no_follow(self, path)
            if path == target and exists and state["git_removed"] and not state["swapped"]:
                state["swapped"] = True
                namespace.rename(moved_namespace)
                namespace.symlink_to(external, target_is_directory=True)
            return exists

    class RemovingGit(FakeGitGateway):
        def remove_worktree(self, repo_root: Path, *, path: Path, force: bool) -> None:
            state["git_removed"] = True
            FakeGitGateway.remove_worktree(self, repo_root, path=path, force=force)

    git = RemovingGit(
        checkout_root=git.checkout_root,
        records=git.records,
        branches=git.branches,
    )
    with pytest.raises(ExpectedError) as caught:
        _service(git, SwapAfterTargetCheckFilesystem()).remove(_request(repo, root, target, force=True))

    assert caught.value.code == "post_remove_cleanup_failed"
    assert caught.value.status == "partial"
    assert state["swapped"]
    assert (moved_namespace / target.name).is_dir()
    assert not (external / target.name).exists()


def test_cleanup_namespace_fd_swap_outside_root_is_blocked(
    tmp_path: Path,
) -> None:
    repo, root, target, base_git = _fixture(tmp_path)
    namespace = root / repo.name
    moved_namespace = tmp_path / f"{repo.name}-outside-cleanup"
    external = tmp_path / "external-cleanup-fd"
    external.mkdir()
    state = {"git_removed": False, "swapped": False}

    class SwapAfterNamespaceOpenFilesystem(FilesystemCliGateway):
        def open_directory(self, path: Path) -> DirectoryHandle:
            directory = FilesystemCliGateway.open_directory(self, path)
            if path == namespace and state["git_removed"] and not state["swapped"]:
                state["swapped"] = True
                namespace.rename(moved_namespace)
                namespace.symlink_to(external, target_is_directory=True)
            return directory

    class RemovingGit(FakeGitGateway):
        def remove_worktree(self, repo_root: Path, *, path: Path, force: bool) -> None:
            state["git_removed"] = True
            FakeGitGateway.remove_worktree(self, repo_root, path=path, force=force)

    git = RemovingGit(
        checkout_root=base_git.checkout_root,
        records=base_git.records,
        branches=base_git.branches,
    )

    with pytest.raises(ExpectedError) as caught:
        _service(git, SwapAfterNamespaceOpenFilesystem()).remove(_request(repo, root, target, force=True))

    assert caught.value.code == "post_remove_cleanup_failed"
    assert caught.value.status == "partial"
    assert state["swapped"]
    assert (moved_namespace / target.name).is_dir()
    assert not (external / target.name).exists()


def test_cleanup_namespace_fd_swap_after_bound_check_is_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, root, target, base_git = _fixture(tmp_path)
    namespace = root / repo.name
    moved_namespace = tmp_path / f"{repo.name}-outside-cleanup-after-check"
    external = tmp_path / "external-cleanup-after-check"
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
        _service(base_git).remove(_request(repo, root, target, force=True))

    assert caught.value.code == "post_remove_cleanup_failed"
    assert caught.value.status == "partial"
    assert swapped
    assert (moved_namespace / target.name).is_dir()
    assert not (external / target.name).exists()


def test_post_git_cleanup_failure_is_partial_and_branch_is_retained(tmp_path: Path) -> None:
    repo, root, target, git = _fixture(tmp_path)

    class FailingCleanupFilesystem(FilesystemCliGateway):
        def remove_target_no_follow_bound(self, directory: DirectoryCapability, path: Path) -> None:
            raise PermissionError("cleanup denied")

    with pytest.raises(ExpectedError) as caught:
        _service(git, FailingCleanupFilesystem()).remove(_request(repo, root, target))

    assert caught.value.code == "post_remove_cleanup_failed"
    assert caught.value.status == "partial"
    result = cast(RemoveResult, caught.value.result)
    assert result.removed_record is True
    assert result.removed_directory is False
    assert result.branch_deleted is False
    assert "feature" in git.branches


def test_leftover_symlink_is_removed_without_following_target(tmp_path: Path) -> None:
    repo, root, target, git = _fixture(tmp_path)
    sentinel = root / repo.name / "sentinel"
    sentinel.mkdir()
    target.rmdir()
    target.symlink_to(sentinel, target_is_directory=True)

    result = _service(git).remove(_request(repo, root, target, force=True))

    assert result.removed_directory is True
    assert not target.exists()
    assert sentinel.is_dir()


def test_broken_leftover_symlink_is_removed_without_following_target(tmp_path: Path) -> None:
    repo, root, target, git = _fixture(tmp_path)
    target.rmdir()
    target.symlink_to(tmp_path / "does-not-exist")

    result = _service(git).remove(_request(repo, root, target, force=True))

    assert result.removed_directory is True
    assert not os.path.lexists(target)


def test_leftover_regular_file_is_removed_after_git_success(tmp_path: Path) -> None:
    repo, root, target, git = _fixture(tmp_path)
    target.rmdir()
    target.write_text("leftover", encoding="utf-8")

    result = _service(git).remove(_request(repo, root, target, force=True))

    assert result.removed_directory is True
    assert not target.exists()


def test_leftover_special_file_is_reported_as_cleanup_partial(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO is unavailable on this platform")
    repo, root, target, git = _fixture(tmp_path)
    target.rmdir()
    os.mkfifo(target)

    with pytest.raises(ExpectedError) as caught:
        _service(git).remove(_request(repo, root, target, force=True))

    assert caught.value.code == "post_remove_cleanup_failed"
    assert caught.value.status == "partial"
    result = cast(RemoveResult, caught.value.result)
    assert result.removed_record is True
    assert result.removed_directory is False
    assert target.exists()


@pytest.mark.parametrize(
    ("changed_branch", "changed_head"),
    [("feature-renamed", None), (None, "fedcba9876543210")],
    ids=["branch", "head"],
)
def test_refresh_branch_or_head_change_is_blocked_before_git_remove(
    tmp_path: Path, changed_branch: str | None, changed_head: str | None
) -> None:
    repo, root, target, base_git = _fixture(tmp_path)
    refreshed = _SnapshotGit(
        checkout_root=base_git.checkout_root,
        records=list(base_git.records),
        branches=set(base_git.branches),
    )
    refreshed.snapshots = (
        list(base_git.records),
        [
            base_git.records[0],
            FakeGitRecord(
                path=target,
                branch=changed_branch if changed_branch is not None else base_git.records[1].branch,
                head=changed_head if changed_head is not None else base_git.records[1].head,
            ),
        ],
    )

    with pytest.raises(ExpectedError) as caught:
        _service(refreshed).remove(_request(repo, root, target))

    assert caught.value.code == "remove_blocked"
    assert "target_changed_after_refresh" in _blockers(caught.value)
    assert not [call for call in refreshed.calls if call[0] == "remove_worktree"]
    assert target.is_dir()


def test_refresh_target_identity_change_is_blocked_before_git_remove(tmp_path: Path) -> None:
    repo, root, target, base_git = _fixture(tmp_path)
    original = tmp_path / "original-target"

    class ReplacingRefreshGit(FakeGitGateway):
        snapshots: tuple[list[FakeGitRecord], list[FakeGitRecord]]
        list_calls = 0

        def worktree_list(self, repo_root: Path) -> list[FakeGitRecord]:
            self._record("worktree_list", repo_root)
            snapshot = self.snapshots[min(self.list_calls, len(self.snapshots) - 1)]
            self.list_calls += 1
            if self.list_calls == 2:
                target.rename(original)
                target.mkdir()
                (target / "replacement-marker").write_text("replacement", encoding="utf-8")
            return list(snapshot)

    refreshed = ReplacingRefreshGit(
        checkout_root=base_git.checkout_root,
        records=list(base_git.records),
        branches=set(base_git.branches),
    )
    refreshed.snapshots = (list(base_git.records), list(base_git.records))
    filesystem = _IdentityFilesystem()

    with pytest.raises(ExpectedError) as caught:
        _service(refreshed, filesystem).remove(_request(repo, root, target, force=True))

    assert caught.value.code == "remove_blocked"
    assert "target_changed_after_refresh" in _blockers(caught.value)
    assert not [call for call in refreshed.calls if call[0] == "remove_worktree"]
    assert target.is_dir()
    assert (target / "replacement-marker").is_file()


def test_post_git_same_basename_replacement_is_not_cleaned(tmp_path: Path) -> None:
    repo, root, target, base_git = _fixture(tmp_path)
    original = tmp_path / "original-target-after-git"

    class ReplacingAfterGit(FakeGitGateway):
        def remove_worktree_bound(
            self, repo_root: Path, *, directory: DirectoryCapability, name: str, force: bool
        ) -> None:
            super().remove_worktree_bound(repo_root, directory=directory, name=name, force=force)
            target.rename(original)
            target.mkdir()
            (target / "sentinel").write_text("must survive", encoding="utf-8")

    git = ReplacingAfterGit(
        checkout_root=base_git.checkout_root,
        records=list(base_git.records),
        branches=set(base_git.branches),
    )
    filesystem = _IdentityFilesystem()

    with pytest.raises(ExpectedError) as caught:
        _service(git, filesystem).remove(_request(repo, root, target, force=True))

    assert caught.value.code == "post_remove_cleanup_failed"
    assert caught.value.status == "partial"
    result = cast(RemoveResult, caught.value.result)
    assert result.removed_record is True
    assert result.removed_directory is False
    assert target.is_dir()
    assert (target / "sentinel").read_text(encoding="utf-8") == "must survive"

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from conftest import FakeBootstrapGateway, FakeGitGateway, FakeGitRecord  # type: ignore[import-not-found]

from worktree_provisioner.application.contracts import ExpectedError, ListRequest
from worktree_provisioner.application.ports import ApplicationPorts, BootstrapGateway, GitGateway
from worktree_provisioner.application.worktree_service import WorktreeService
from worktree_provisioner.infra.environment import EnvironmentAdapter
from worktree_provisioner.infra.filesystem import FilesystemCliGateway


def _service(git: FakeGitGateway) -> WorktreeService:
    return WorktreeService(
        ApplicationPorts(
            git=cast(GitGateway, git),
            bootstrap=cast(BootstrapGateway, FakeBootstrapGateway()),
            filesystem=FilesystemCliGateway(),
            environment=EnvironmentAdapter(),
        )
    )


def test_inventory_preserves_flags_and_deterministic_blockers(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    root = tmp_path / "root"
    namespace = root / "checkout"
    namespace.mkdir(parents=True)
    managed = namespace / "checkout-feature"
    managed.mkdir()
    nested = namespace / "nested" / "checkout-nested"
    nested.mkdir(parents=True)
    locked = namespace / "checkout-locked"
    locked.mkdir()
    external = tmp_path / "external"
    external.mkdir()

    records = [
        FakeGitRecord(path=repo, head="main-head", branch="main"),
        FakeGitRecord(path=managed, head="feature-head", branch="feature"),
        FakeGitRecord(path=nested, head="nested-head", branch="nested"),
        FakeGitRecord(path=external, head="external-head", branch="external"),
        FakeGitRecord(
            path=locked,
            head="locked-head",
            branch="locked",
            locked=True,
            lock_reason="busy",
        ),
        FakeGitRecord(
            path=namespace / "checkout-stale",
            head="stale-head",
            branch="stale",
            detached=True,
        ),
        FakeGitRecord(
            path=namespace / "checkout-bare",
            head=None,
            branch=None,
            bare=True,
        ),
    ]
    git = FakeGitGateway(checkout_root=repo, records=records)

    result = _service(git).list(ListRequest(repo_root=repo, root=root))
    by_branch = {record.branch: record for record in result.worktrees}

    assert by_branch["main"].id == "main"
    assert by_branch["main"].main is True
    assert by_branch["main"].current is True
    assert by_branch["main"].remove_blockers == ("main_worktree", "current_worktree", "outside_managed_namespace")

    assert by_branch["feature"].id == "feature"
    assert by_branch["feature"].managed is True
    assert by_branch["feature"].origin == "managed_namespace"
    assert by_branch["feature"].removable is True
    assert by_branch["feature"].remove_blockers == ()

    assert by_branch["nested"].managed is True
    assert by_branch["nested"].origin == "managed_namespace"
    assert by_branch["nested"].removable is False
    assert by_branch["nested"].remove_blockers == ("nested_target_unsupported",)

    assert by_branch["external"].origin == "external"
    assert by_branch["external"].managed is False
    assert by_branch["external"].remove_blockers == ("outside_managed_namespace",)

    assert by_branch["locked"].locked is True
    assert by_branch["locked"].lock_reason == "busy"
    assert by_branch["locked"].remove_blockers == ("locked_worktree",)

    assert by_branch["stale"].path_exists is False
    assert by_branch["stale"].detached is True
    assert by_branch["stale"].remove_blockers == ("path_missing",)

    assert by_branch[None].bare is True
    assert by_branch[None].remove_blockers == ("bare_worktree", "path_missing")


def test_missing_namespace_classifies_existing_records_as_external(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    root = tmp_path / "root"
    prospective = root / "checkout" / "checkout-feature"
    prospective.mkdir(parents=True)

    class MissingNamespaceFilesystem(FilesystemCliGateway):
        def lstat_kind(self, path: Path) -> str:
            if path == root / "checkout":
                return "missing"
            return super().lstat_kind(path)

    git = FakeGitGateway(
        checkout_root=repo,
        records=[FakeGitRecord(path=repo), FakeGitRecord(path=prospective, branch="feature")],
    )
    service = WorktreeService(
        ApplicationPorts(
            git=cast(GitGateway, git),
            bootstrap=cast(BootstrapGateway, FakeBootstrapGateway()),
            filesystem=MissingNamespaceFilesystem(),
            environment=EnvironmentAdapter(),
        )
    )

    result = service.list(ListRequest(repo_root=repo, root=root))

    assert result.worktrees[1].classification_available is True
    assert result.worktrees[1].classification_reason == "root_valid"
    assert result.worktrees[1].managed is False
    assert result.worktrees[1].origin == "external"
    assert result.worktrees[1].remove_blockers == ("outside_managed_namespace",)


def test_namespace_symlink_keeps_inventory_observable_but_unavailable(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "redirected"
    target.mkdir()
    namespace = root / "checkout"
    namespace.symlink_to(target, target_is_directory=True)
    linked = target / "checkout-feature"
    linked.mkdir()
    git = FakeGitGateway(
        checkout_root=repo,
        records=[FakeGitRecord(path=repo), FakeGitRecord(path=linked, branch="feature")],
    )

    result = _service(git).list(ListRequest(repo_root=repo, root=root))

    for record in result.worktrees:
        assert record.classification_available is False
        assert record.classification_reason == "namespace_symlink"
        assert record.origin == "classification_unavailable"
        assert record.managed is False
        assert record.removable is False
        assert record.remove_blockers[-1] == "classification_unavailable"


def test_inventory_distinguishes_unknown_path_observation_from_missing(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    root = tmp_path / "root"
    namespace = root / "checkout"
    namespace.mkdir(parents=True)
    target = namespace / "checkout-feature"
    target.mkdir()

    class FailingPathObservationFilesystem(FilesystemCliGateway):
        def path_exists_no_follow(self, path: Path) -> bool:
            if path == target:
                raise OSError("path observation interrupted")
            return super().path_exists_no_follow(path)

    git = FakeGitGateway(
        checkout_root=repo,
        records=[
            FakeGitRecord(path=repo, branch="main"),
            FakeGitRecord(path=target, branch="feature"),
        ],
    )
    service = WorktreeService(
        ApplicationPorts(
            git=cast(GitGateway, git),
            bootstrap=cast(BootstrapGateway, FakeBootstrapGateway()),
            filesystem=FailingPathObservationFilesystem(),
            environment=EnvironmentAdapter(),
        )
    )

    result = service.list(ListRequest(repo_root=repo, root=root))
    record = next(item for item in result.worktrees if item.branch == "feature")

    assert record.path_exists is None
    assert record.removable is False
    assert record.managed is True
    assert record.origin == "managed_namespace"


def test_main_id_is_reserved_and_suffix_shaped_ids_remain_unique(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    root = tmp_path / "root"
    namespace = root / "checkout"
    namespace.mkdir(parents=True)
    first_external = tmp_path / "a" / "main"
    first_external.mkdir(parents=True)
    suffix_external = tmp_path / "b" / "main~2"
    suffix_external.mkdir(parents=True)
    git = FakeGitGateway(
        checkout_root=repo,
        records=[
            FakeGitRecord(path=repo),
            FakeGitRecord(path=suffix_external, branch="suffix"),
            FakeGitRecord(path=first_external, branch="first"),
        ],
    )

    result = _service(git).list(ListRequest(repo_root=repo, root=root))
    by_branch = {record.branch: record.id for record in result.worktrees}

    assert by_branch["main"] == "main"
    assert by_branch["first"] == "main~2"
    assert by_branch["suffix"] == "main~2~2"
    assert len(set(by_branch.values())) == 3


def test_namespace_other_is_an_unsafe_inventory_error(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / "checkout").write_text("unsafe", encoding="utf-8")
    git = FakeGitGateway(checkout_root=repo, records=[FakeGitRecord(path=repo)])

    with pytest.raises(ExpectedError) as caught:
        _service(git).list(ListRequest(repo_root=repo, root=root))

    assert caught.value.code == "unsafe_namespace"
    assert caught.value.operation == "list"


def test_git_list_failure_is_converted_to_list_error(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()

    class FailingGit(FakeGitGateway):
        def worktree_list(self, repo_root: Path) -> list[FakeGitRecord]:
            raise RuntimeError("git worktree list failed")

    git = FailingGit(checkout_root=repo, records=[])

    with pytest.raises(ExpectedError) as caught:
        _service(git).list(ListRequest(repo_root=repo, root=tmp_path / "root"))

    assert caught.value.code == "git_worktree_list_failed"
    assert caught.value.operation == "list"

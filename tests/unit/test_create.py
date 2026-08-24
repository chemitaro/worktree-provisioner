from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from conftest import (  # type: ignore[import-not-found]
    FakeBootstrapGateway,
    FakeBootstrapResult,
    FakeFilesystemGateway,
    FakeGitGateway,
    FakeGitRecord,
)

from worktree_provisioner.application.contracts import (
    BootstrapResult,
    CreateRequest,
    CreateResult,
    ExpectedError,
)
from worktree_provisioner.application.ports import ApplicationPorts, BootstrapGateway, GitGateway
from worktree_provisioner.application.worktree_service import WorktreeService
from worktree_provisioner.infra.environment import EnvironmentAdapter
from worktree_provisioner.infra.filesystem import FilesystemCliGateway
from worktree_provisioner.infra.git_cli import GitAdapterError
from worktree_provisioner.presentation.json_v1 import error_document
from worktree_provisioner.presentation.text import render_error


def _ports(git, bootstrap, filesystem) -> ApplicationPorts:
    return ApplicationPorts(
        git=cast("GitGateway", git),
        bootstrap=cast("BootstrapGateway", bootstrap),
        filesystem=filesystem,
        environment=EnvironmentAdapter(),
    )


def _request(repo: Path, root: Path, *, label: str | None = None, bootstrap: bool = False) -> CreateRequest:
    return CreateRequest(repo_root=repo, root=root, label=label, bootstrap_enabled=bootstrap)


def test_create_uses_main_record_basename_and_current_branch(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    root = tmp_path / "root"
    main = FakeGitRecord(path=repo, head="abc", branch="main")
    git = FakeGitGateway(
        checkout_root=repo,
        current_branch="feature/current",
        records=[main],
        branches={"feature/current"},
    )
    filesystem = FakeFilesystemGateway()
    bootstrap = FakeBootstrapGateway()

    result = WorktreeService(_ports(git, bootstrap, filesystem)).create(_request(repo, root, label="setup"))

    assert result.id == "setup"
    assert result.container_path == root / "checkout"
    assert result.worktree_path == root / "checkout" / "checkout-setup"
    assert result.branch == "feature/current-setup"
    assert bootstrap.calls == []


def test_no_bootstrap_never_calls_bootstrap_gateway(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = FakeGitGateway(
        checkout_root=repo,
        current_branch="main",
        records=[FakeGitRecord(path=repo)],
        branches={"main"},
    )
    bootstrap = FakeBootstrapGateway(result=FakeBootstrapResult(status="failed", exit_code=7))
    filesystem = FakeFilesystemGateway()

    result = WorktreeService(_ports(git, bootstrap, filesystem)).create(_request(repo, tmp_path / "root"))

    assert result.bootstrap.status == "disabled"
    assert bootstrap.calls == []


@pytest.mark.parametrize("status", ["skipped", "succeeded"])
def test_successful_bootstrap_states_return_complete_result(tmp_path: Path, status: str) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = FakeGitGateway(checkout_root=repo, records=[FakeGitRecord(path=repo)], branches={"main"})
    bootstrap = FakeBootstrapGateway(
        result=FakeBootstrapResult(status=status, exit_code=0 if status == "succeeded" else None)
    )
    filesystem = FakeFilesystemGateway()

    result = WorktreeService(_ports(git, bootstrap, filesystem)).create(
        _request(repo, tmp_path / "root", bootstrap=True)
    )

    assert result.bootstrap.status == status
    assert result.bootstrap.requested is True


@pytest.mark.parametrize(
    ("status", "exit_code", "partial"),
    [("succeeded", 0, False), ("detection_failed", 2, True), ("failed", 7, True)],
)
def test_bootstrap_terminal_result_reobserves_mutated_artifacts(
    tmp_path: Path, status: str, exit_code: int, partial: bool
) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = FakeGitGateway(checkout_root=repo, records=[FakeGitRecord(path=repo)], branches={"main"})
    filesystem = FakeFilesystemGateway()

    class MutatingBootstrap(FakeBootstrapGateway):
        def run_make_init_if_available(self, worktree_path: Path) -> BootstrapResult:
            self.calls.append(worktree_path)
            add_call = next(call for call in reversed(git.calls) if call[0] == "add_worktree")
            branch = cast(str, add_call[2]["branch"])
            git.branches.discard(branch)
            git.records = []
            filesystem.existing.add(worktree_path)
            return super().run_make_init_if_available(worktree_path)

    bootstrap = MutatingBootstrap(result=FakeBootstrapResult(status=status, exit_code=exit_code))
    service = WorktreeService(_ports(git, bootstrap, filesystem))

    if partial:
        with pytest.raises(ExpectedError) as caught:
            service.create(_request(repo, tmp_path / "root", bootstrap=True))
        result = cast("CreateResult", caught.value.result)
    else:
        result = service.create(_request(repo, tmp_path / "root", bootstrap=True))

    assert result.artifacts.container_exists is True
    assert result.artifacts.worktree_path_exists is True
    assert result.artifacts.branch_exists is False
    assert result.artifacts.worktree_record_exists is False
    assert result.bootstrap.status == status


def test_bootstrap_failure_is_partial_with_retained_result(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = FakeGitGateway(checkout_root=repo, records=[FakeGitRecord(path=repo)], branches={"main"})
    bootstrap = FakeBootstrapGateway(result=FakeBootstrapResult(status="failed", exit_code=7, detail="init failed"))
    filesystem = FakeFilesystemGateway()

    with pytest.raises(ExpectedError) as caught:
        WorktreeService(_ports(git, bootstrap, filesystem)).create(_request(repo, tmp_path / "root", bootstrap=True))

    error = caught.value
    assert error.code == "bootstrap_failed"
    assert error.status == "partial"
    result = cast("CreateResult", error.result)
    assert result.worktree_path.is_absolute()
    assert result.bootstrap.status == "failed"
    assert len(git.calls) >= 1


def test_unknown_git_add_failure_does_not_retry_or_cleanup(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = FakeGitGateway(
        checkout_root=repo,
        records=[FakeGitRecord(path=repo)],
        branches={"main"},
        add_error=RuntimeError("permission denied while creating worktree"),
    )
    filesystem = FakeFilesystemGateway()

    with pytest.raises(ExpectedError) as caught:
        WorktreeService(_ports(git, FakeBootstrapGateway(), filesystem)).create(_request(repo, tmp_path / "root"))

    assert caught.value.code == "git_worktree_add_failed"
    assert caught.value.status == "error"
    assert sum(1 for call in git.calls if call[0] == "add_worktree") == 1
    assert not any(call[0] == "remove_worktree" for call in git.calls)
    artifacts = cast("dict[str, object]", caught.value.details["artifacts"])
    assert set(artifacts) == {
        "container_exists",
        "worktree_path_exists",
        "branch_exists",
        "worktree_record_exists",
    }


@dataclass
class RetryOnceGit(FakeGitGateway):
    attempts: int = 0

    def add_worktree(self, repo_root: Path, *, path: Path, branch: str) -> None:
        self._record("add_worktree", repo_root, path=path, branch=branch)
        self.attempts += 1
        if self.attempts == 1:
            raise GitAdapterError(
                operation="add_worktree",
                argv=("git", "worktree", "add"),
                message="known branch collision",
                collision_kind="branch",
            )
        self.branches.add(branch)
        self.records.append(FakeGitRecord(path=path, branch=branch))


def test_known_git_collision_retries_next_candidate(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = RetryOnceGit(checkout_root=repo, records=[FakeGitRecord(path=repo)], branches={"main"})
    filesystem = FakeFilesystemGateway()

    result = WorktreeService(_ports(git, FakeBootstrapGateway(), filesystem)).create(_request(repo, tmp_path / "root"))

    assert result.id == "wt2"
    assert result.branch == "main-wt2"
    assert git.attempts == 2


@dataclass
class CollisionThenTerminalErrorGit(FakeGitGateway):
    attempts: int = 0

    def add_worktree(self, repo_root: Path, *, path: Path, branch: str) -> None:
        self._record("add_worktree", repo_root, path=path, branch=branch)
        self.attempts += 1
        if self.attempts == 1:
            self.branches.add(branch)
            raise GitAdapterError(
                operation="add_worktree",
                argv=("git", "worktree", "add"),
                message="known branch collision",
                collision_kind="branch",
            )
        raise GitAdapterError(
            operation="add_worktree",
            argv=("git", "worktree", "add"),
            message="later ref-lock failure",
            diagnostic="fatal: ref lock already exists",
        )


def test_retry_warning_facts_reach_later_terminal_error_json_and_text(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = CollisionThenTerminalErrorGit(checkout_root=repo, records=[FakeGitRecord(path=repo)], branches={"main"})
    filesystem = FakeFilesystemGateway()

    with pytest.raises(ExpectedError) as caught:
        WorktreeService(_ports(git, FakeBootstrapGateway(), filesystem)).create(_request(repo, tmp_path / "root"))

    error = caught.value
    assert error.code == "git_worktree_add_failed"
    assert len(error.warnings) == 1
    warning = error.warnings[0]
    assert warning.code == "collision_partial_artifact"
    assert warning.facts == {
        "candidate_id": "wt1",
        "branch": "main-wt1",
        "path": str(tmp_path / "root" / "checkout" / "checkout-wt1"),
        "path_exists": False,
        "branch_exists": True,
        "worktree_record_exists": False,
    }
    document = error_document(error)
    assert document["warnings"] == [{"code": warning.code, "message": warning.message, "facts": dict(warning.facts)}]
    _stdout, stderr = render_error(error)
    assert "code=collision_partial_artifact" in stderr
    assert "facts=branch=main-wt1" in stderr


class BranchColliderRaceGit(FakeGitGateway):
    attempts: int = 0

    def add_worktree(self, repo_root: Path, *, path: Path, branch: str) -> None:
        self._record("add_worktree", repo_root, path=path, branch=branch)
        self.attempts += 1
        if self.attempts == 1:
            # The branch is created by a concurrent actor after preflight and
            # is the exact object Git reports as the refusal cause.
            self.branches.add(branch)
            raise GitAdapterError(
                operation="add_worktree",
                argv=("git", "worktree", "add"),
                message="branch race",
                collision_kind="branch",
            )
        self.branches.add(branch)
        self.records.append(FakeGitRecord(path=path, branch=branch))


class PathColliderRaceGit(FakeGitGateway):
    attempts: int = 0
    filesystem: FakeFilesystemGateway

    def add_worktree(self, repo_root: Path, *, path: Path, branch: str) -> None:
        self._record("add_worktree", repo_root, path=path, branch=branch)
        self.attempts += 1
        if self.attempts == 1:
            # A concurrent actor creates the candidate directory before Git
            # reaches its path check.
            self.filesystem.existing.add(path)
            raise GitAdapterError(
                operation="add_worktree",
                argv=("git", "worktree", "add"),
                message="path race",
                collision_kind="path",
            )
        self.branches.add(branch)
        self.records.append(FakeGitRecord(path=path, branch=branch))


class CheckedOutColliderRaceGit(FakeGitGateway):
    attempts: int = 0

    def add_worktree(self, repo_root: Path, *, path: Path, branch: str) -> None:
        self._record("add_worktree", repo_root, path=path, branch=branch)
        self.attempts += 1
        if self.attempts == 1:
            # A concurrent worktree claims the candidate branch at another
            # path.  The candidate path itself remains absent.
            self.branches.add(branch)
            self.records.append(FakeGitRecord(path=repo_root.parent / "claimed", branch=branch))
            raise GitAdapterError(
                operation="add_worktree",
                argv=("git", "worktree", "add"),
                message="checked-out race",
                collision_kind="checked_out",
            )
        self.branches.add(branch)
        self.records.append(FakeGitRecord(path=path, branch=branch))


class BranchColliderWithRecordRaceGit(FakeGitGateway):
    attempts: int = 0

    def add_worktree(self, repo_root: Path, *, path: Path, branch: str) -> None:
        self._record("add_worktree", repo_root, path=path, branch=branch)
        self.attempts += 1
        if self.attempts == 1:
            # A concurrent actor checks out the same branch elsewhere.  The
            # branch record is the collider, not an artifact at this attempt's
            # candidate path.
            self.branches.add(branch)
            self.records.append(FakeGitRecord(path=repo_root.parent / "claimed", branch=branch))
            raise GitAdapterError(
                operation="add_worktree",
                argv=("git", "worktree", "add"),
                message="branch collision with checked-out collider",
                collision_kind="branch",
            )
        self.branches.add(branch)
        self.records.append(FakeGitRecord(path=path, branch=branch))


@pytest.mark.parametrize("gateway", ["branch", "path", "checked_out"])
def test_typed_collision_retries_when_refusal_collider_is_observed(tmp_path: Path, gateway: str) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    filesystem = FakeFilesystemGateway()
    if gateway == "branch":
        git: FakeGitGateway = BranchColliderRaceGit(
            checkout_root=repo, records=[FakeGitRecord(path=repo)], branches={"main"}
        )
    elif gateway == "path":
        path_git = PathColliderRaceGit(checkout_root=repo, records=[FakeGitRecord(path=repo)], branches={"main"})
        path_git.filesystem = filesystem
        git = path_git
    else:
        git = CheckedOutColliderRaceGit(checkout_root=repo, records=[FakeGitRecord(path=repo)], branches={"main"})

    result = WorktreeService(_ports(git, FakeBootstrapGateway(), filesystem)).create(_request(repo, tmp_path / "root"))

    assert result.id == "wt2"
    assert result.branch == "main-wt2"
    assert sum(1 for call in git.calls if call[0] == "add_worktree") == 2


def test_branch_collision_with_same_branch_record_at_other_path_retries(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = BranchColliderWithRecordRaceGit(
        checkout_root=repo,
        records=[FakeGitRecord(path=repo)],
        branches={"main"},
    )
    filesystem = FakeFilesystemGateway()

    result = WorktreeService(_ports(git, FakeBootstrapGateway(), filesystem)).create(_request(repo, tmp_path / "root"))

    assert result.id == "wt2"
    assert result.branch == "main-wt2"
    assert sum(1 for call in git.calls if call[0] == "add_worktree") == 2


def test_generic_collision_fragment_is_not_a_retry_signal(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = FakeGitGateway(
        checkout_root=repo,
        records=[FakeGitRecord(path=repo)],
        branches={"main"},
        add_error=GitAdapterError(
            operation="add_worktree",
            argv=("git", "worktree", "add"),
            message="unknown Git failure",
            diagnostic="fatal: ref lock already exists",
        ),
    )
    filesystem = FakeFilesystemGateway()

    with pytest.raises(ExpectedError) as caught:
        WorktreeService(_ports(git, FakeBootstrapGateway(), filesystem)).create(_request(repo, tmp_path / "root"))

    assert caught.value.code == "git_worktree_add_failed"
    assert sum(1 for call in git.calls if call[0] == "add_worktree") == 1
    assert cast("dict[str, object]", caught.value.details["artifacts"]) == {
        "container_exists": True,
        "worktree_path_exists": False,
        "branch_exists": False,
        "worktree_record_exists": False,
    }


@dataclass
class PartialTypedCollisionGit(FakeGitGateway):
    def add_worktree(self, repo_root: Path, *, path: Path, branch: str) -> None:
        self._record("add_worktree", repo_root, path=path, branch=branch)
        self.branches.add(branch)
        self.records.append(FakeGitRecord(path=path, branch=branch))
        raise GitAdapterError(
            operation="add_worktree",
            argv=("git", "worktree", "add"),
            message="typed collision with partial branch",
            collision_kind="branch",
        )


def test_typed_collision_with_observed_artifact_is_not_retried(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = PartialTypedCollisionGit(checkout_root=repo, records=[FakeGitRecord(path=repo)], branches={"main"})
    filesystem = FakeFilesystemGateway()

    with pytest.raises(ExpectedError) as caught:
        WorktreeService(_ports(git, FakeBootstrapGateway(), filesystem)).create(_request(repo, tmp_path / "root"))

    assert caught.value.code == "git_worktree_add_failed"
    assert sum(1 for call in git.calls if call[0] == "add_worktree") == 1
    artifacts = cast("dict[str, object]", caught.value.details["artifacts"])
    assert artifacts["branch_exists"] is True


def test_detached_current_head_is_rejected_before_mutation(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = FakeGitGateway(checkout_root=repo, current_branch=None, records=[FakeGitRecord(path=repo)])
    filesystem = FakeFilesystemGateway()

    with pytest.raises(ExpectedError) as caught:
        WorktreeService(_ports(git, FakeBootstrapGateway(), filesystem)).create(_request(repo, tmp_path / "root"))

    assert caught.value.code == "detached_head"
    assert not any(call[0] == "add_worktree" for call in git.calls)


def test_namespace_file_is_rejected_before_directory_creation(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    root = tmp_path / "root"
    (root / "checkout").parent.mkdir(parents=True)
    (root / "checkout").write_text("unsafe", encoding="utf-8")
    git = FakeGitGateway(checkout_root=repo, records=[FakeGitRecord(path=repo)])
    filesystem = FilesystemCliGateway()

    with pytest.raises(ExpectedError) as caught:
        WorktreeService(_ports(git, FakeBootstrapGateway(), filesystem)).create(_request(repo, root))

    assert caught.value.code == "unsafe_namespace"
    assert (root / "checkout").is_file()


@pytest.mark.parametrize("label", ["", "UPPER", "issue_1", "issue/1", "issue.1", "issue 1"])
def test_invalid_label_matrix_has_no_mutation_or_bootstrap_calls(tmp_path: Path, label: str) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = FakeGitGateway(checkout_root=repo, records=[FakeGitRecord(path=repo)], branches={"main"})
    bootstrap = FakeBootstrapGateway()
    filesystem = FakeFilesystemGateway()

    with pytest.raises(ExpectedError) as caught:
        WorktreeService(_ports(git, bootstrap, filesystem)).create(
            _request(repo, tmp_path / "root", label=label, bootstrap=True)
        )

    assert caught.value.code == "invalid_label"
    assert not any(call[0] == "add_worktree" for call in git.calls)
    assert not any(call[0] == "ensure_directory" for call in filesystem.calls)
    assert bootstrap.calls == []


def test_invalid_generated_branch_ref_has_no_mutation(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = FakeGitGateway(
        checkout_root=repo,
        records=[FakeGitRecord(path=repo)],
        branches={"main"},
        valid_refs=set(),
    )
    bootstrap = FakeBootstrapGateway()
    filesystem = FakeFilesystemGateway()

    with pytest.raises(ExpectedError) as caught:
        WorktreeService(_ports(git, bootstrap, filesystem)).create(_request(repo, tmp_path / "root", bootstrap=True))

    assert caught.value.code == "git_worktree_add_failed"
    assert not any(call[0] == "add_worktree" for call in git.calls)
    assert not any(call[0] == "ensure_directory" for call in filesystem.calls)
    assert bootstrap.calls == []


class AlwaysCandidateCollisionFilesystem(FakeFilesystemGateway):
    def path_exists_no_follow(self, path: Path) -> bool:
        self.calls.append(("path_exists_no_follow", path))
        return path.name.startswith("checkout-wt")


def test_candidate_ceiling_is_finite_and_patchable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import worktree_provisioner.application.worktree_service as service_module

    repo = tmp_path / "checkout"
    repo.mkdir()
    git = FakeGitGateway(checkout_root=repo, records=[FakeGitRecord(path=repo)], branches={"main"})
    bootstrap = FakeBootstrapGateway()
    filesystem = AlwaysCandidateCollisionFilesystem()
    monkeypatch.setattr(service_module, "MAX_CANDIDATE_ATTEMPTS", 3)

    with pytest.raises(ExpectedError) as caught:
        WorktreeService(_ports(git, bootstrap, filesystem)).create(_request(repo, tmp_path / "root"))

    assert caught.value.code == "candidate_exhausted"
    assert caught.value.details["attempts"] == 3
    assert not any(call[0] == "add_worktree" for call in git.calls)
    assert not any(call[0] == "ensure_directory" for call in filesystem.calls)
    assert bootstrap.calls == []


@pytest.mark.parametrize("status", ["detection_failed", "failed"])
def test_bootstrap_detection_and_execution_failures_are_partial_without_rollback(tmp_path: Path, status: str) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = FakeGitGateway(checkout_root=repo, records=[FakeGitRecord(path=repo)], branches={"main"})
    bootstrap = FakeBootstrapGateway(
        result=FakeBootstrapResult(status=status, exit_code=2 if status == "detection_failed" else 7)
    )
    filesystem = FakeFilesystemGateway()

    with pytest.raises(ExpectedError) as caught:
        WorktreeService(_ports(git, bootstrap, filesystem)).create(_request(repo, tmp_path / "root", bootstrap=True))

    error = caught.value
    assert error.code == ("bootstrap_detection_failed" if status == "detection_failed" else "bootstrap_failed")
    assert error.status == "partial"
    result = cast("CreateResult", error.result)
    assert result.worktree_path.is_absolute()
    assert result.branch in git.branches
    assert bootstrap.calls == [result.worktree_path]
    assert not any(call[0] == "remove_worktree" for call in git.calls)


@pytest.mark.parametrize(
    ("detail", "exit_code"),
    [("make executable was not found", None), ("missing.mk: No such file", 2)],
)
def test_bootstrap_detection_failure_variants_are_partial(tmp_path: Path, detail: str, exit_code: int | None) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = FakeGitGateway(checkout_root=repo, records=[FakeGitRecord(path=repo)], branches={"main"})
    bootstrap = FakeBootstrapGateway(
        result=FakeBootstrapResult(status="detection_failed", exit_code=exit_code, detail=detail)
    )
    filesystem = FakeFilesystemGateway()

    with pytest.raises(ExpectedError) as caught:
        WorktreeService(_ports(git, bootstrap, filesystem)).create(_request(repo, tmp_path / "root", bootstrap=True))

    assert caught.value.code == "bootstrap_detection_failed"
    assert caught.value.status == "partial"
    result = cast("CreateResult", caught.value.result)
    assert result.bootstrap.status == "detection_failed"
    assert result.bootstrap.detail == detail


@dataclass
class PartialArtifactGit(FakeGitGateway):
    artifact_state: str = "none"
    after_add: bool = False
    failed_path: Path | None = None
    failed_branch: str | None = None

    def add_worktree(self, repo_root: Path, *, path: Path, branch: str) -> None:
        self._record("add_worktree", repo_root, path=path, branch=branch)
        self.failed_path = path
        self.failed_branch = branch
        self.after_add = True
        raise RuntimeError("I/O failure after partial Git mutation")

    def local_branch_exists(self, repo_root: Path, branch: str) -> bool:
        self._record("local_branch_exists", repo_root, branch)
        if self.after_add and branch == self.failed_branch:
            if self.artifact_state == "branch-unavailable":
                raise RuntimeError("branch inspection unavailable")
            return "branch" in self.artifact_state
        return branch in self.branches

    def worktree_list(self, repo_root: Path) -> list[FakeGitRecord]:
        self._record("worktree_list", repo_root)
        if self.after_add and self.artifact_state == "record-unavailable":
            raise RuntimeError("record inspection unavailable")
        records = list(self.records)
        if self.after_add and self.artifact_state == "record":
            assert self.failed_path is not None
            records.append(FakeGitRecord(path=self.failed_path, branch=self.failed_branch))
        return records


@dataclass
class PartialArtifactFilesystem(FakeFilesystemGateway):
    git: PartialArtifactGit | None = None
    artifact_state: str = "none"

    def path_exists_no_follow(self, path: Path) -> bool:
        self.calls.append(("path_exists_no_follow", path))
        if self.git is not None and self.git.after_add and path == self.git.failed_path:
            if self.artifact_state == "path-unavailable":
                raise RuntimeError("path inspection unavailable")
            return "path" in self.artifact_state
        return path in self.existing


@pytest.mark.parametrize(
    ("artifact_state", "expected"),
    [
        ("branch", (True, False, False)),
        ("path", (False, True, False)),
        ("record", (False, False, True)),
        ("branch+path", (True, True, False)),
    ],
)
def test_git_add_partial_artifacts_are_observable_without_cleanup(
    tmp_path: Path, artifact_state: str, expected: tuple[bool, bool, bool]
) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = PartialArtifactGit(
        checkout_root=repo,
        records=[FakeGitRecord(path=repo)],
        branches={"main"},
        artifact_state=artifact_state,
    )
    filesystem = PartialArtifactFilesystem(git=git, artifact_state=artifact_state)

    with pytest.raises(ExpectedError) as caught:
        WorktreeService(_ports(git, FakeBootstrapGateway(), filesystem)).create(_request(repo, tmp_path / "root"))

    assert caught.value.code == "git_worktree_add_failed"
    artifacts = cast("dict[str, object]", caught.value.details["artifacts"])
    assert artifacts["container_exists"] is True
    assert (
        artifacts["branch_exists"],
        artifacts["worktree_path_exists"],
        artifacts["worktree_record_exists"],
    ) == expected
    assert not any(call[0] == "remove_worktree" for call in git.calls)
    assert not any(call[0] == "remove_target_no_follow" for call in filesystem.calls)


@pytest.mark.parametrize("unavailable", ["branch-unavailable", "path-unavailable", "record-unavailable"])
def test_git_add_partial_inspection_failure_is_null_not_false(tmp_path: Path, unavailable: str) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    git = PartialArtifactGit(
        checkout_root=repo,
        records=[FakeGitRecord(path=repo)],
        branches={"main"},
        artifact_state=unavailable,
    )
    filesystem = PartialArtifactFilesystem(git=git, artifact_state=unavailable)

    with pytest.raises(ExpectedError) as caught:
        WorktreeService(_ports(git, FakeBootstrapGateway(), filesystem)).create(_request(repo, tmp_path / "root"))

    artifacts = cast("dict[str, object]", caught.value.details["artifacts"])
    field = {
        "branch-unavailable": "branch_exists",
        "path-unavailable": "worktree_path_exists",
        "record-unavailable": "worktree_record_exists",
    }[unavailable]
    assert artifacts[field] is None

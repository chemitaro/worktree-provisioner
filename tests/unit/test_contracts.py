from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, is_dataclass
from inspect import get_annotations
from pathlib import Path
from typing import get_args, get_origin

import pytest

from worktree_provisioner.application.contracts import (
    ArtifactState,
    BlockerCode,
    BootstrapResult,
    BootstrapStatus,
    CreateRequest,
    CreateResult,
    ErrorCode,
    ExpectedError,
    GitWorktreeRecord,
    ListRequest,
    ListResult,
    RemoveRequest,
    RemoveResult,
    ShowRequest,
    ShowResult,
    WorktreeOrigin,
    WorktreeProvisionerError,
    WorktreeRecordView,
)
from worktree_provisioner.application.ports import (
    ApplicationPorts,
    BootstrapGateway,
    EnvironmentGateway,
    FilesystemGateway,
    GitGateway,
    Ports,
)


def _record(path: Path = Path("/repo")) -> GitWorktreeRecord:
    return GitWorktreeRecord(path=path, head="0123456789abcdef", branch="main")


def _view(path: Path = Path("/worktrees/repo/repo-feature")) -> WorktreeRecordView:
    return WorktreeRecordView(
        id="feature",
        path=path,
        basename=path.name,
        branch="main-feature",
        head="0123456789abcdef",
        detached=False,
        bare=False,
        locked=False,
        lock_reason=None,
        main=False,
        current=False,
        path_exists=True,
        record_exists=True,
        managed=True,
        classification_available=True,
        classification_reason="root_valid",
        origin="managed_namespace",
        removable=True,
        remove_blockers=(),
    )


def test_literal_contracts_expose_stable_values() -> None:
    assert get_origin(BootstrapStatus) is None or get_args(BootstrapStatus)
    assert set(get_args(BootstrapStatus)) == {
        "disabled",
        "skipped",
        "succeeded",
        "failed",
        "detection_failed",
    }
    assert set(get_args(ErrorCode)) == {
        "usage_error",
        "git_unavailable",
        "repository_unavailable",
        "bare_repository_unsupported",
        "root_required",
        "invalid_root",
        "unsafe_namespace",
        "invalid_label",
        "detached_head",
        "git_worktree_list_failed",
        "candidate_exhausted",
        "container_create_failed",
        "git_worktree_add_failed",
        "bootstrap_detection_failed",
        "bootstrap_failed",
        "target_not_found",
        "ambiguous_target",
        "unsupported_branch_target",
        "remove_blocked",
        "git_worktree_remove_failed",
        "git_worktree_remove_partial",
        "post_remove_cleanup_failed",
        "internal_error",
    }
    assert "outside_managed_namespace" in get_args(BlockerCode)
    assert "classification_unavailable" in get_args(WorktreeOrigin)


def test_dataclasses_are_frozen_and_slot_based() -> None:
    objects = (
        _record(),
        BootstrapResult(requested=False, status="disabled", command=None, exit_code=None, detail=None),
        ArtifactState(True, False, None, True),
        CreateRequest(Path("/repo"), Path("/root"), None, True),
        ListRequest(Path("/repo"), Path("/root")),
        ShowRequest(Path("/repo"), Path("/root"), "feature"),
        RemoveRequest(Path("/repo"), Path("/root"), "feature", False),
    )
    for value in objects:
        assert is_dataclass(value)
        assert type(value).__dataclass_params__.frozen  # type: ignore[union-attr]
        assert not hasattr(value, "__dict__")
        with pytest.raises(FrozenInstanceError):
            value.__class__.__setattr__(value, next(iter(fields(value))).name, None)


def test_git_record_preserves_flags_and_lock_reason() -> None:
    record = GitWorktreeRecord(
        path=Path("/repo/locked"),
        head=None,
        branch=None,
        detached=True,
        bare=True,
        locked=True,
        lock_reason="maintenance",
    )

    assert record.detached is True
    assert record.bare is True
    assert record.locked is True
    assert record.lock_reason == "maintenance"


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        (
            BootstrapResult(requested=False, status="disabled", command=None, exit_code=None, detail=None),
            (False, "disabled", None, None),
        ),
        (
            BootstrapResult(requested=True, status="skipped", command=None, exit_code=None, detail=None),
            (True, "skipped", None, None),
        ),
        (
            BootstrapResult(requested=True, status="succeeded", command=("make", "init"), exit_code=0, detail=None),
            (True, "succeeded", ("make", "init"), 0),
        ),
        (
            BootstrapResult(
                requested=True,
                status="detection_failed",
                command=("make", "-n", "init"),
                exit_code=None,
                detail="make unavailable",
            ),
            (True, "detection_failed", ("make", "-n", "init"), None),
        ),
        (
            BootstrapResult(
                requested=True,
                status="failed",
                command=("make", "init"),
                exit_code=7,
                detail="failed",
            ),
            (True, "failed", ("make", "init"), 7),
        ),
    ],
)
def test_bootstrap_states_and_nullability(result: BootstrapResult, expected: tuple[object, ...]) -> None:
    assert (result.requested, result.status, result.command, result.exit_code) == expected


def test_bootstrap_rejects_inconsistent_state() -> None:
    with pytest.raises(ValueError):
        BootstrapResult(requested=True, status="disabled", command=None, exit_code=None, detail=None)
    with pytest.raises(ValueError):
        BootstrapResult(requested=True, status="succeeded", command=("make", "init"), exit_code=1, detail=None)
    with pytest.raises(ValueError):
        BootstrapResult(requested=True, status="failed", command=("make", "init"), exit_code=0, detail=None)


def test_result_objects_keep_paths_and_immutable_sequences() -> None:
    bootstrap = BootstrapResult(requested=False, status="disabled", command=None, exit_code=None, detail=None)
    artifacts = ArtifactState(True, True, True, True)
    created = CreateResult(
        id="feature",
        main_worktree_path=Path("/repo"),
        container_path=Path("/worktrees/repo"),
        worktree_path=Path("/worktrees/repo/repo-feature"),
        branch="main-feature",
        bootstrap=bootstrap,
        artifacts=artifacts,
    )
    listed = ListResult((_view(),))
    shown = ShowResult("feature", listed.worktrees[0])
    removed = RemoveResult("feature", shown.worktree, False, True, True, False)

    assert isinstance(created.worktree_path, Path)
    assert listed.worktrees == (shown.worktree,)
    assert removed.resolved_target is shown.worktree
    assert removed.branch_deleted is False


def test_expected_error_is_structured_and_alias_is_exact() -> None:
    result = ListResult(())
    error = ExpectedError(
        code="target_not_found",
        operation="show",
        message="worktree target was not found",
        details={"target": "missing"},
        result=result,
        status="error",
    )

    assert isinstance(error, RuntimeError)
    assert WorktreeProvisionerError is ExpectedError
    assert str(error) == error.message
    assert error.code == "target_not_found"
    assert error.operation == "show"
    assert error.details == {"target": "missing"}
    assert error.result is result
    assert error.status == "error"


def test_ports_are_runtime_protocols_with_narrow_surface() -> None:
    assert getattr(GitGateway, "_is_protocol", False)
    assert getattr(BootstrapGateway, "_is_protocol", False)
    assert getattr(FilesystemGateway, "_is_protocol", False)
    assert getattr(EnvironmentGateway, "_is_protocol", False)
    assert set(get_annotations(GitGateway)) == set()
    assert set(GitGateway.__dict__) >= {
        "resolve_checkout_root",
        "current_branch_or_none",
        "local_branch_exists",
        "check_branch_ref",
        "worktree_list",
        "add_worktree",
        "remove_worktree",
    }
    assert Ports is ApplicationPorts

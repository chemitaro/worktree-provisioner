from __future__ import annotations

from pathlib import Path

from worktree_provisioner.application.contracts import (
    ArtifactState,
    BootstrapResult,
    CreateResult,
    ExpectedError,
    RemoveResult,
    ResultWarning,
    WorktreeRecordView,
)
from worktree_provisioner.presentation.text import render_error, render_success


def _view() -> WorktreeRecordView:
    return WorktreeRecordView(
        id="feature",
        path=Path("relative/repo-feature"),
        basename="repo-feature",
        branch="main-feature",
        head="head",
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


def _create() -> CreateResult:
    return CreateResult(
        id="setup",
        main_worktree_path=Path("repo"),
        container_path=Path("worktrees/repo"),
        worktree_path=Path("worktrees/repo/repo-setup"),
        branch="main-setup",
        bootstrap=BootstrapResult(
            requested=True,
            status="failed",
            command=("make", "init"),
            exit_code=7,
            detail="failed",
        ),
        artifacts=ArtifactState(True, True, True, True),
    )


def test_complete_text_uses_product_prefix_and_absolute_paths() -> None:
    output = render_success(_create())
    assert output.startswith("worktree-provisioner: ok (create)")
    assert "path=/" in output
    assert "bootstrap status=failed" in output


def test_partial_create_text_separates_facts_and_error() -> None:
    error = ExpectedError(
        code="bootstrap_failed",
        operation="create",
        message="make init failed; worktree was retained",
        details={},
        result=_create(),
        status="partial",
    )
    stdout, stderr = render_error(error)
    assert stdout.startswith("worktree-provisioner: partial (create)")
    assert "path=/" in stdout
    assert stderr == "worktree-provisioner: error: make init failed; worktree was retained"


def test_partial_remove_text_preserves_mutation_facts() -> None:
    target = _view()
    result = RemoveResult(
        target="feature",
        resolved_target=target,
        force_requested=True,
        removed_record=True,
        removed_directory=False,
        branch_deleted=False,
    )
    error = ExpectedError(
        code="post_remove_cleanup_failed",
        operation="remove",
        message="Git worktree record was removed but target cleanup failed",
        details={},
        result=result,
        status="partial",
    )
    stdout, stderr = render_error(error)
    assert stdout.startswith("worktree-provisioner: partial (remove)")
    assert "path=/" in stdout
    assert "removed_record=True" in stdout
    assert "removed_directory=False" in stdout
    assert stderr.startswith("worktree-provisioner: error:")


def test_create_warning_is_rendered_for_stderr() -> None:
    result = _create()
    result = CreateResult(
        id=result.id,
        main_worktree_path=result.main_worktree_path,
        container_path=result.container_path,
        worktree_path=result.worktree_path,
        branch=result.branch,
        bootstrap=result.bootstrap,
        artifacts=result.artifacts,
        warnings=(ResultWarning(code="collision_partial_artifact", message="retained wt1"),),
    )
    error = ExpectedError(
        code="bootstrap_failed",
        operation="create",
        message="make init failed; worktree was retained",
        details={},
        result=result,
        status="partial",
    )

    _stdout, stderr = render_error(error)

    assert "worktree-provisioner: warning: code=collision_partial_artifact message=retained wt1" in stderr

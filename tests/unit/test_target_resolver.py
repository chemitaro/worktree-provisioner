from __future__ import annotations

from pathlib import Path

import pytest

from worktree_provisioner.application.contracts import WorktreeRecordView
from worktree_provisioner.application.target_resolver import TargetResolutionError, resolve_target


def _record(*, id: str, path: str, basename: str, branch: str) -> WorktreeRecordView:
    return WorktreeRecordView(
        id=id,
        path=Path(path),
        basename=basename,
        branch=branch,
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


def test_resolver_uses_id_before_basename_ambiguity(tmp_path: Path) -> None:
    first = _record(id="stable", path=str(tmp_path / "one"), basename="same", branch="one-branch")
    second = _record(id="other", path=str(tmp_path / "two"), basename="same", branch="two-branch")

    assert resolve_target((first, second), "stable") is first


def test_resolver_accepts_canonical_absolute_path(tmp_path: Path) -> None:
    path = tmp_path / "target"
    path.mkdir()
    record = _record(id="target", path=str(path), basename="target", branch="feature")

    assert resolve_target((record,), str(path / ".." / "target")) is record


def test_resolver_rejects_ambiguous_basename_with_full_candidates(tmp_path: Path) -> None:
    first = _record(id="left", path=str(tmp_path / "left"), basename="same", branch="left")
    second = _record(id="right", path=str(tmp_path / "right"), basename="same", branch="right")

    with pytest.raises(TargetResolutionError) as caught:
        resolve_target((first, second), "same")

    assert caught.value.code == "ambiguous_target"
    assert caught.value.details["candidates"] == [first, second]


def test_resolver_reports_branch_only_selector_and_not_found(tmp_path: Path) -> None:
    record = _record(id="feature", path=str(tmp_path / "feature"), basename="feature", branch="feature-branch")

    with pytest.raises(TargetResolutionError) as branch_error:
        resolve_target((record,), "feature-branch")
    assert branch_error.value.code == "unsupported_branch_target"

    with pytest.raises(TargetResolutionError) as missing_error:
        resolve_target((record,), "missing")
    assert missing_error.value.code == "target_not_found"


def test_resolver_matches_absolute_path_before_branch_diagnostic(tmp_path: Path) -> None:
    path = tmp_path / "feature-branch"
    record = _record(id="feature", path=str(path), basename=path.name, branch=path.name)

    assert resolve_target((record,), str(path)) is record

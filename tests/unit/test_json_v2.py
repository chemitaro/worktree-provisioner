from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast, get_args

from worktree_provisioner.application.contracts import (
    ArtifactState,
    BlockerCode,
    BootstrapResult,
    CreateResult,
    ErrorCode,
    ExpectedError,
    GitWorktreeRecord,
    ListResult,
    RemoveResult,
    ResultWarning,
    ShowResult,
    WorktreeRecordView,
)
from worktree_provisioner.presentation.json_v2 import (
    dumps,
    envelope,
    error_document,
    json_value,
    list_payload,
    remove_payload,
    show_payload,
    success_document,
    usage_error_document,
    worktree_payload,
)


def _view() -> WorktreeRecordView:
    return WorktreeRecordView(
        id="feature",
        path=Path("relative/worktrees/repo-feature"),
        basename="repo-feature",
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


def test_create_builder_is_explicit_and_makes_paths_absolute() -> None:
    result = CreateResult(
        id="feature",
        main_worktree_path=Path("repo"),
        container_path=Path("worktrees/repo"),
        worktree_path=Path("worktrees/repo/repo-feature"),
        branch="main-feature",
        bootstrap=BootstrapResult(requested=True, status="skipped", command=None, exit_code=None, detail=None),
        artifacts=ArtifactState(True, None, True, False),
    )

    document = success_document(result)
    payload = cast(dict[str, Any], document["result"])
    assert set(document) == {"schema_version", "status", "operation", "result", "error", "warnings"}
    assert document["status"] == "ok"
    assert document["operation"] == "create"
    assert document["error"] is None
    assert document["warnings"] == []
    assert payload["worktree_path"].startswith("/")
    assert payload["bootstrap"] == {
        "requested": True,
        "status": "skipped",
        "command": None,
        "exit_code": None,
        "detail": None,
    }
    assert payload["artifacts"] == {
        "container_exists": True,
        "worktree_path_exists": None,
        "branch_exists": True,
        "worktree_record_exists": False,
    }


def test_error_builder_explicitly_converts_nested_candidates_and_nulls_result() -> None:
    first = _view()
    second = WorktreeRecordView(
        id="feature~2",
        path=Path("/other/repo-feature"),
        basename=first.basename,
        branch="other-feature",
        head=None,
        detached=True,
        bare=False,
        locked=True,
        lock_reason="busy",
        main=False,
        current=False,
        path_exists=False,
        record_exists=True,
        managed=False,
        classification_available=True,
        classification_reason="root_valid",
        origin="external",
        removable=False,
        remove_blockers=("outside_managed_namespace",),
    )
    error = ExpectedError(
        code="ambiguous_target",
        operation="show",
        message="ambiguous",
        details={"target": "repo-feature", "candidates": [first, second]},
        result=ListResult((first, second)),
        status="error",
    )

    document = error_document(error)
    payload = cast(dict[str, Any], document["error"])
    assert document["status"] == "error"
    assert document["result"] is None
    assert payload["code"] == "ambiguous_target"
    assert payload["details"]["candidates"][0]["path"].startswith("/")
    assert payload["details"]["candidates"][1]["locked"] is True
    assert payload["details"]["candidates"][1]["remove_blockers"] == ["outside_managed_namespace"]


def test_worktree_payload_preserves_unknown_path_observation_as_json_null() -> None:
    unknown_path = WorktreeRecordView(
        id="unknown",
        path=Path("relative/worktrees/repo-unknown"),
        basename="repo-unknown",
        branch="unknown",
        head=None,
        detached=False,
        bare=False,
        locked=False,
        lock_reason=None,
        main=False,
        current=False,
        path_exists=None,
        record_exists=True,
        managed=True,
        classification_available=True,
        classification_reason="root_valid",
        origin="managed_namespace",
        removable=False,
        remove_blockers=("path_observation_unavailable",),
    )

    document = success_document(ListResult((unknown_path,)))
    payload = cast(dict[str, Any], document["result"])
    listed = cast(list[Any], payload["worktrees"])
    assert cast(dict[str, Any], listed[0])["path_exists"] is None

    serialized = json.loads(dumps(document))
    assert serialized["result"]["worktrees"][0]["path_exists"] is None


def test_all_public_error_codes_are_emittable() -> None:
    codes = get_args(ErrorCode)
    assert codes
    for code in codes:
        error = ExpectedError(
            code=cast(Any, code),
            operation="show",
            message="diagnostic",
            details={"typed": True},
            result=None,
            status="error",
        )
        document = error_document(error)
        assert document["schema_version"] == 2
        assert document["status"] == "error"
        assert document["result"] is None
        assert cast(dict[str, Any], document["error"])["code"] == code
        assert document["warnings"] == []


def test_dumps_escapes_surrogateescape_paths_as_reversible_utf8() -> None:
    surrogate_path = "/tmp/repo-\udcff"

    serialized = dumps({"path": surrogate_path, "label": "日本語"})

    assert serialized.encode("utf-8")
    assert r"\udcff" in serialized
    assert all(ord(character) < 128 for character in serialized)
    assert json.loads(serialized)["path"] == surrogate_path
    assert json.loads(serialized)["label"] == "日本語"


def test_json_value_restores_valid_utf8_surrogateescape_paths() -> None:
    utf8_surrogate_path = b"/tmp/repo-\xe6\x97\xa5\xe6\x9c\xac".decode("ascii", errors="surrogateescape")
    mixed_surrogate_path = b"/tmp/repo-\xe6\x97\xa5-\xff".decode("ascii", errors="surrogateescape")
    raw_surrogate_path = b"/tmp/repo-\xff".decode("ascii", errors="surrogateescape")
    utf8_surrogate_branch = b"feature-\xe6\x97\xa5".decode("ascii", errors="surrogateescape")

    converted = cast(
        dict[str, Any],
        json_value({"path": utf8_surrogate_path, "mixed": mixed_surrogate_path, "raw": raw_surrogate_path}),
    )
    record = cast(
        dict[str, Any],
        json_value(
            GitWorktreeRecord(
                path=Path(utf8_surrogate_path),
                head=None,
                branch=utf8_surrogate_branch,
            )
        ),
    )

    assert converted["path"] == "/tmp/repo-日本"
    assert converted["mixed"] == "/tmp/repo-日-\udcff"
    assert converted["raw"] == raw_surrogate_path
    assert record["branch"] == "feature-日"


def test_all_blocker_codes_are_explicitly_typed_in_worktree_payload() -> None:
    blockers = get_args(BlockerCode)
    for blocker in blockers:
        base = _view()
        view = WorktreeRecordView(
            id=base.id,
            path=base.path,
            basename=base.basename,
            branch=base.branch,
            head=base.head,
            detached=base.detached,
            bare=base.bare,
            locked=base.locked,
            lock_reason=base.lock_reason,
            main=base.main,
            current=base.current,
            path_exists=base.path_exists,
            record_exists=base.record_exists,
            managed=base.managed,
            classification_available=base.classification_available,
            classification_reason=base.classification_reason,
            origin=base.origin,
            removable=False,
            remove_blockers=(cast(BlockerCode, blocker),),
        )
        payload = worktree_payload(view)
        assert payload["remove_blockers"] == [blocker]
        assert isinstance(payload["removable"], bool)


def test_inventory_show_and_remove_builders_have_explicit_fields() -> None:
    view = _view()
    listed = list_payload(ListResult((view,)))
    shown = show_payload(ShowResult("feature", view))
    removed = remove_payload(
        RemoveResult(
            target="feature",
            resolved_target=view,
            force_requested=True,
            removed_record=True,
            removed_directory=False,
            branch_deleted=False,
        )
    )

    assert set(listed) == {"worktrees"}
    assert set(shown) == {"target", "worktree"}
    assert set(removed) == {
        "target",
        "resolved_target",
        "force_requested",
        "removed_record",
        "removed_directory",
        "branch_deleted",
    }
    listed_worktree = cast(dict[str, Any], cast(list[Any], listed["worktrees"])[0])
    assert all(isinstance(listed_worktree[name], bool) for name in ("detached", "bare", "locked"))
    assert all(
        isinstance(removed[name], bool)
        for name in ("force_requested", "removed_record", "removed_directory", "branch_deleted")
    )

    partial = error_document(
        ExpectedError(
            code="post_remove_cleanup_failed",
            operation="remove",
            message="cleanup failed",
            details={"target": view},
            result=RemoveResult(
                target="feature",
                resolved_target=view,
                force_requested=False,
                removed_record=True,
                removed_directory=False,
                branch_deleted=False,
            ),
            status="partial",
        )
    )
    partial_result = cast(dict[str, Any], partial["result"])
    partial_error = cast(dict[str, Any], partial["error"])
    assert partial["status"] == "partial"
    assert partial_result["removed_record"] is True
    assert partial_result["removed_directory"] is False
    assert partial_error["code"] == "post_remove_cleanup_failed"


def test_warning_objects_are_reduced_to_code_and_message() -> None:
    document = envelope(
        status="ok",
        operation="list",
        result={"worktrees": []},
        error=None,
        warnings=({"code": "diagnostic", "message": "a warning", "ignored": "field"},),
    )

    assert document["warnings"] == [{"code": "diagnostic", "message": "a warning"}]


def test_create_result_warnings_are_emitted_at_envelope_level() -> None:
    result = CreateResult(
        id="wt2",
        main_worktree_path=Path("/repo"),
        container_path=Path("/worktrees/repo"),
        worktree_path=Path("/worktrees/repo/repo-wt2"),
        branch="main-wt2",
        bootstrap=BootstrapResult(requested=False, status="disabled", command=None, exit_code=None, detail=None),
        artifacts=ArtifactState(True, True, True, True),
        warnings=(ResultWarning(code="collision_partial_artifact", message="retained wt1"),),
    )

    document = success_document(result)

    assert document["warnings"] == [{"code": "collision_partial_artifact", "message": "retained wt1"}]
    assert "warnings" not in cast(dict[str, Any], document["result"])


def test_partial_keeps_result_and_usage_error_is_versioned() -> None:
    result = CreateResult(
        id="setup",
        main_worktree_path=Path("/repo"),
        container_path=Path("/worktrees/repo"),
        worktree_path=Path("/worktrees/repo/repo-setup"),
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
    partial = ExpectedError(
        code="bootstrap_failed",
        operation="create",
        message="retained",
        details={"bootstrap": json_value(result.bootstrap)},
        result=result,
        status="partial",
    )
    document = error_document(partial)
    assert document["status"] == "partial"
    assert document["result"] is not None
    assert document["error"] is not None

    usage = usage_error_document("missing target", operation="show")
    assert usage == {
        "schema_version": 2,
        "status": "error",
        "operation": "show",
        "result": None,
        "error": {
            "code": "usage_error",
            "message": "invalid command-line usage",
            "details": {"diagnostic": "missing target"},
        },
        "warnings": [],
    }

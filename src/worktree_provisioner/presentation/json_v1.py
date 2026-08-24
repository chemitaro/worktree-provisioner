"""Versioned JSON schema v1 for the command-line boundary.

The application layer deliberately exposes immutable dataclasses and
``pathlib.Path`` values.  This module is the single, explicit conversion
boundary to the machine contract; it does not rely on implicit dataclass
flattening so field names, nullability, and path handling remain reviewable.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from worktree_provisioner.application.contracts import (
    ArtifactState,
    BootstrapResult,
    CreateResult,
    ExpectedError,
    GitWorktreeRecord,
    ListResult,
    RemoveResult,
    ShowResult,
    WorktreeRecordView,
)

SCHEMA_VERSION: Final[int] = 1


def success_document(result: object) -> dict[str, object]:
    """Build an ``ok`` envelope for one known operation result."""

    return envelope(
        status="ok",
        operation=operation_for_result(result),
        result=result,
        error=None,
    )


def error_document(error: ExpectedError) -> dict[str, object]:
    """Build an expected operational-error envelope."""

    result = error.result if error.status == "partial" else None
    return envelope(
        status=error.status,
        operation=error.operation,
        result=result,
        error=error,
    )


def usage_error_document(message: str, *, operation: str | None = None) -> dict[str, object]:
    """Build the parser-error document used when ``--json`` was explicit."""

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "error",
        "operation": operation,
        "result": None,
        "error": {
            "code": "usage_error",
            "message": "invalid command-line usage",
            "details": {"diagnostic": _bounded(message)},
        },
        "warnings": [],
    }


def envelope(
    *,
    status: str,
    operation: str | None,
    result: object | None,
    error: ExpectedError | None,
    warnings: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    """Build the common v1 envelope with all fields present."""

    if error is None:
        error_payload: object | None = None
    else:
        error_payload = {
            "code": error.code,
            "message": error.message,
            "details": json_value(error.details),
        }

    warning_payloads: list[dict[str, object]] = []
    for warning in warnings:
        warning_payloads.append(
            {
                "code": str(warning.get("code", "diagnostic")),
                "message": str(warning.get("message", "")),
            }
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "operation": operation,
        "result": json_value(result) if result is not None else None,
        "error": error_payload,
        "warnings": warning_payloads,
    }


def dumps(document: Mapping[str, object]) -> str:
    """Serialize exactly one v1 document without a trailing human message."""

    return json.dumps(document, ensure_ascii=False)


def operation_for_result(result: object) -> str:
    if isinstance(result, CreateResult):
        return "create"
    if isinstance(result, ListResult):
        return "list"
    if isinstance(result, ShowResult):
        return "show"
    if isinstance(result, RemoveResult):
        return "remove"
    raise TypeError(f"unsupported command result: {type(result).__name__}")


def json_value(value: object) -> object:
    """Convert supported application values without implicit dataclass flattening."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return _absolute_path(value)
    if isinstance(value, BootstrapResult):
        return bootstrap_payload(value)
    if isinstance(value, ArtifactState):
        return artifact_payload(value)
    if isinstance(value, WorktreeRecordView):
        return worktree_payload(value)
    if isinstance(value, GitWorktreeRecord):
        return git_record_payload(value)
    if isinstance(value, CreateResult):
        return create_payload(value)
    if isinstance(value, ListResult):
        return list_payload(value)
    if isinstance(value, ShowResult):
        return show_payload(value)
    if isinstance(value, RemoveResult):
        return remove_payload(value)
    if isinstance(value, Mapping):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [json_value(item) for item in value]
    if isinstance(value, set):
        return [json_value(item) for item in sorted(value, key=str)]
    # Details should normally contain only the types above.  A bounded string
    # is safer than exposing an arbitrary object's private attributes.
    return _bounded(str(value))


def bootstrap_payload(result: BootstrapResult) -> dict[str, object]:
    return {
        "requested": result.requested,
        "status": result.status,
        "command": list(result.command) if result.command is not None else None,
        "exit_code": result.exit_code,
        "detail": result.detail,
    }


def artifact_payload(result: ArtifactState) -> dict[str, bool | None]:
    return {
        "container_exists": result.container_exists,
        "worktree_path_exists": result.worktree_path_exists,
        "branch_exists": result.branch_exists,
        "worktree_record_exists": result.worktree_record_exists,
    }


def git_record_payload(result: GitWorktreeRecord) -> dict[str, object]:
    return {
        "path": _absolute_path(result.path),
        "head": result.head,
        "branch": result.branch,
        "detached": result.detached,
        "bare": result.bare,
        "locked": result.locked,
        "lock_reason": result.lock_reason,
    }


def worktree_payload(result: WorktreeRecordView) -> dict[str, object]:
    return {
        "id": result.id,
        "path": _absolute_path(result.path),
        "basename": result.basename,
        "branch": result.branch,
        "head": result.head,
        "detached": result.detached,
        "bare": result.bare,
        "locked": result.locked,
        "lock_reason": result.lock_reason,
        "main": result.main,
        "current": result.current,
        "path_exists": result.path_exists,
        "record_exists": result.record_exists,
        "managed": result.managed,
        "classification_available": result.classification_available,
        "classification_reason": result.classification_reason,
        "origin": result.origin,
        "removable": result.removable,
        "remove_blockers": list(result.remove_blockers),
    }


def create_payload(result: CreateResult) -> dict[str, object]:
    return {
        "id": result.id,
        "main_worktree_path": _absolute_path(result.main_worktree_path),
        "container_path": _absolute_path(result.container_path),
        "worktree_path": _absolute_path(result.worktree_path),
        "branch": result.branch,
        "bootstrap": bootstrap_payload(result.bootstrap),
        "artifacts": artifact_payload(result.artifacts),
    }


def list_payload(result: ListResult) -> dict[str, object]:
    return {"worktrees": [worktree_payload(item) for item in result.worktrees]}


def show_payload(result: ShowResult) -> dict[str, object]:
    return {"target": result.target, "worktree": worktree_payload(result.worktree)}


def remove_payload(result: RemoveResult) -> dict[str, object]:
    return {
        "target": result.target,
        "resolved_target": worktree_payload(result.resolved_target),
        "force_requested": result.force_requested,
        "removed_record": result.removed_record,
        "removed_directory": result.removed_directory,
        "branch_deleted": result.branch_deleted,
    }


def _absolute_path(path: Path) -> str:
    candidate = path.expanduser()
    return str(candidate if candidate.is_absolute() else candidate.absolute())


def _bounded(value: str, limit: int = 4096) -> str:
    if len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return f"{value[: limit - 1]}…"


__all__ = [
    "SCHEMA_VERSION",
    "artifact_payload",
    "bootstrap_payload",
    "create_payload",
    "dumps",
    "envelope",
    "error_document",
    "git_record_payload",
    "json_value",
    "list_payload",
    "operation_for_result",
    "remove_payload",
    "show_payload",
    "success_document",
    "usage_error_document",
    "worktree_payload",
]

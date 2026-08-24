"""Pure target resolution for inventory records.

The resolver intentionally knows nothing about Git or the command line.  It
operates on one inventory snapshot and applies the stable selector order from
the product contract: id, canonical absolute path, basename, then a branch
diagnostic.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from worktree_provisioner.application.contracts import WorktreeRecordView
from worktree_provisioner.application.root_and_naming import canonical_path


class TargetResolutionError(ValueError):
    """A target cannot be resolved to exactly one inventory record."""

    __slots__ = ("code", "details")

    code: str
    details: dict[str, object]

    def __init__(self, *, code: str, message: str, details: dict[str, object]) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def resolve_target(records: Sequence[WorktreeRecordView], target: str) -> WorktreeRecordView:
    """Resolve ``target`` using the fixed, deterministic selector priority."""

    by_id = [record for record in records if record.id == target]
    if by_id:
        # IDs are made unique by inventory construction.  Keep the defensive
        # ambiguity path so a caller cannot accidentally first-match a broken
        # snapshot.
        if len(by_id) > 1:
            raise TargetResolutionError(
                code="ambiguous_target",
                message=f"worktree target is ambiguous: {target}",
                details={"target": target, "candidates": list(by_id)},
            )
        return by_id[0]

    target_path: Path | None = None
    try:
        candidate_path = Path(target)
    except (TypeError, ValueError):
        candidate_path = None
    if candidate_path is not None and candidate_path.is_absolute():
        target_path = canonical_path(candidate_path)
        path_matches = [record for record in records if canonical_path(record.path) == target_path]
        if len(path_matches) == 1:
            return path_matches[0]
        if len(path_matches) > 1:
            raise TargetResolutionError(
                code="ambiguous_target",
                message=f"worktree target is ambiguous: {target}",
                details={"target": target, "candidates": list(path_matches)},
            )

    if candidate_path is None or not candidate_path.is_absolute():
        basename_matches = [record for record in records if record.basename == target]
        if len(basename_matches) == 1:
            return basename_matches[0]
        if len(basename_matches) > 1:
            raise TargetResolutionError(
                code="ambiguous_target",
                message=f"worktree target is ambiguous: {target}",
                details={"target": target, "candidates": list(basename_matches)},
            )

    if any(record.branch == target for record in records):
        raise TargetResolutionError(
            code="unsupported_branch_target",
            message=f"branch names are not supported as worktree targets: {target}",
            details={"target": target, "branch": target},
        )

    raise TargetResolutionError(
        code="target_not_found",
        message=f"worktree target was not found: {target}",
        details={"target": target},
    )


__all__ = ["TargetResolutionError", "resolve_target"]

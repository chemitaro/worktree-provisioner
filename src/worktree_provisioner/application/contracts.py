"""Application-layer value objects and expected-error contracts.

The application layer deliberately keeps these objects independent from the
CLI and from any concrete adapter.  Paths therefore remain :class:`Path`
instances here; presentation/JSON conversion belongs to a later layer.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypeAlias

BootstrapStatus: TypeAlias = Literal[
    "disabled",
    "skipped",
    "succeeded",
    "failed",
    "detection_failed",
]
ResponseStatus: TypeAlias = Literal["ok", "partial", "error"]
ErrorStatus: TypeAlias = Literal["partial", "error"]
ClassificationReason: TypeAlias = Literal["root_valid", "namespace_symlink"]
WorktreeOrigin: TypeAlias = Literal[
    "managed_namespace",
    "external",
    "classification_unavailable",
]
Operation: TypeAlias = Literal["create", "list", "show", "remove"]
CollisionKind: TypeAlias = Literal["branch", "path", "checked_out"]

ErrorCode: TypeAlias = Literal[
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
]

BlockerCode: TypeAlias = Literal[
    "main_worktree",
    "current_worktree",
    "bare_worktree",
    "locked_worktree",
    "path_missing",
    "path_observation_unavailable",
    "record_missing_after_refresh",
    "target_changed_after_refresh",
    "outside_managed_namespace",
    "classification_unavailable",
    "nested_target_unsupported",
    "protected_cleanup_path",
    "unsafe_namespace",
]


@dataclass(frozen=True, slots=True)
class GitWorktreeRecord:
    """A single record returned by ``git worktree list --porcelain``."""

    path: Path
    head: str | None
    branch: str | None
    detached: bool = False
    bare: bool = False
    locked: bool = False
    lock_reason: str | None = None


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """Observable state of optional ``make init`` bootstrap."""

    requested: bool
    status: BootstrapStatus
    command: tuple[str, ...] | None
    exit_code: int | None
    detail: str | None

    def __post_init__(self) -> None:
        """Reject combinations that cannot be represented by the contract."""

        if self.status == "disabled" and self.requested:
            raise ValueError("disabled bootstrap must not be requested")
        if self.status != "disabled" and not self.requested:
            raise ValueError("non-disabled bootstrap must be requested")

        if self.status in {"disabled", "skipped"}:
            if self.command is not None or self.exit_code is not None:
                raise ValueError(f"{self.status} bootstrap cannot have command or exit_code")
        elif self.command is None:
            raise ValueError(f"{self.status} bootstrap requires a command")

        if self.status == "succeeded" and self.exit_code != 0:
            raise ValueError("succeeded bootstrap must have exit_code=0")
        if self.status == "failed" and (self.exit_code is None or self.exit_code == 0):
            raise ValueError("failed bootstrap must have a non-zero exit_code")


@dataclass(frozen=True, slots=True)
class ArtifactState:
    """Best-effort post-operation artifact observations.

    ``None`` is intentionally distinct from ``False``: it means that the
    corresponding inspection could not be completed.
    """

    container_exists: bool | None
    worktree_path_exists: bool | None
    branch_exists: bool | None
    worktree_record_exists: bool | None


@dataclass(frozen=True, slots=True)
class ResultWarning:
    """A bounded machine-readable warning attached to an operation result."""

    code: str
    message: str
    # Stable facts are separate from the human diagnostic.  Machine consumers
    # must be able to report retained collision artifacts without parsing the
    # bounded message string.
    facts: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WorktreeRecordView:
    """Inventory record enriched with classification and removal policy facts."""

    id: str
    path: Path
    basename: str
    branch: str | None
    head: str | None
    detached: bool
    bare: bool
    locked: bool
    lock_reason: str | None
    main: bool
    current: bool
    path_exists: bool | None
    record_exists: bool
    managed: bool
    classification_available: bool
    classification_reason: ClassificationReason
    origin: WorktreeOrigin
    removable: bool
    remove_blockers: tuple[BlockerCode, ...]


@dataclass(frozen=True, slots=True)
class CreateRequest:
    repo_root: Path
    root: Path
    label: str | None
    bootstrap_enabled: bool


@dataclass(frozen=True, slots=True)
class CreateResult:
    id: str
    main_worktree_path: Path
    container_path: Path
    worktree_path: Path
    branch: str
    bootstrap: BootstrapResult
    artifacts: ArtifactState
    warnings: tuple[ResultWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class ListRequest:
    repo_root: Path
    root: Path


@dataclass(frozen=True, slots=True)
class ListResult:
    worktrees: tuple[WorktreeRecordView, ...]


@dataclass(frozen=True, slots=True)
class ShowRequest:
    repo_root: Path
    root: Path
    target: str


@dataclass(frozen=True, slots=True)
class ShowResult:
    target: str
    worktree: WorktreeRecordView


@dataclass(frozen=True, slots=True)
class RemoveRequest:
    repo_root: Path
    root: Path
    target: str
    force: bool


@dataclass(frozen=True, slots=True)
class RemoveResult:
    target: str
    resolved_target: WorktreeRecordView
    force_requested: bool
    removed_record: bool
    removed_directory: bool
    branch_deleted: bool


class ExpectedError(RuntimeError):
    """Structured operational error expected by the CLI boundary."""

    __slots__ = ("code", "details", "message", "operation", "result", "status", "warnings")

    code: ErrorCode
    operation: Operation | None
    message: str
    details: Mapping[str, object]
    result: object | None
    status: ErrorStatus
    warnings: tuple[ResultWarning, ...]

    def __init__(
        self,
        *,
        code: ErrorCode,
        operation: Operation | None,
        message: str,
        details: Mapping[str, object],
        result: object | None,
        status: ErrorStatus,
        warnings: tuple[ResultWarning, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.operation = operation
        self.message = message
        self.details = details
        self.result = result
        self.status = status
        self.warnings = tuple(warnings)

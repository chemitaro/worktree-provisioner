"""Root policy and deterministic create-candidate helpers.

The module contains the small pieces of policy which are shared by the CLI
composition root and the create application service.  It deliberately does
not read process environment variables itself; environment access remains an
adapter concern and the caller supplies the selected value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from worktree_provisioner.application.contracts import ErrorCode
from worktree_provisioner.application.ports import EnvironmentGateway, FilesystemGateway

ROOT_ENVIRONMENT: Final[str] = "WORKTREE_PROVISIONER_ROOT"
LABEL_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9-]+$")
MAX_CANDIDATE_ATTEMPTS: Final[int] = 10_000
RETRYABLE_GIT_COLLISION_KINDS: Final[frozenset[str]] = frozenset({"branch", "path", "checked_out"})


class RootResolutionError(ValueError):
    """A root source or root filesystem entry violates the product policy."""

    __slots__ = ("code", "details")

    code: ErrorCode
    details: dict[str, object]

    def __init__(self, *, code: ErrorCode, message: str, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class LabelValidationError(ValueError):
    """A create label is absent or outside the public label grammar."""


@dataclass(frozen=True, slots=True)
class WorktreeCandidate:
    """One deterministic id/path/branch candidate for a create attempt."""

    id: str
    path: Path
    branch: str


def select_root(
    explicit: str | Path | None,
    environment: EnvironmentGateway,
) -> Path:
    """Select and syntactically validate the configured root.

    An explicit value always wins, including an empty or invalid value.  The
    lower-precedence environment value is therefore never consulted in that
    case.  The returned path is absolute but intentionally keeps the lexical
    final entry.  This lets :func:`validate_root` classify an existing or
    broken symlink with ``lstat`` before canonicalizing an accepted directory
    link.
    """

    raw: str | None
    if explicit is not None:
        raw = str(explicit)
        source = "--root"
    else:
        raw = environment.getenv(ROOT_ENVIRONMENT)
        source = ROOT_ENVIRONMENT

    if raw is None or not raw.strip():
        raise RootResolutionError(
            code="root_required",
            message=f"worktree root is required; pass --root or set {ROOT_ENVIRONMENT}",
            details={"source": source},
        )

    expanded = Path(raw).expanduser()
    if not expanded.is_absolute():
        raise RootResolutionError(
            code="invalid_root",
            message=f"worktree root must be an absolute path: {raw!r}",
            details={"source": source, "root": raw, "reason": "relative_path"},
        )
    return expanded


def validate_root(
    root: Path,
    filesystem: FilesystemGateway,
    *,
    allow_missing: bool = True,
) -> Path:
    """Validate root kind without following a root symlink accidentally.

    A directory symlink is an accepted root *source*, but the returned value
    is its canonical directory.  Broken links, files, special entries, and a
    missing root when ``allow_missing`` is false are rejected.
    """

    if not isinstance(root, Path) or not root.is_absolute():
        raise RootResolutionError(
            code="invalid_root",
            message=f"worktree root must be an absolute path: {root}",
            details={"root": str(root), "reason": "relative_path"},
        )

    kind = filesystem.lstat_kind(root)
    if kind == "missing":
        if not allow_missing:
            raise RootResolutionError(
                code="invalid_root",
                message=f"worktree root does not exist: {root}",
                details={"root": str(root), "reason": "missing"},
            )
        try:
            return root.resolve(strict=False)
        except OSError as exc:
            raise RootResolutionError(
                code="invalid_root",
                message=f"failed to resolve worktree root: {root}",
                details={"root": str(root), "reason": str(exc)},
            ) from exc

    if kind == "directory":
        return _canonical_root(root)

    if kind == "symlink":
        canonical = _canonical_root(root)
        target_kind = filesystem.lstat_kind(canonical)
        if target_kind == "directory":
            return canonical
        raise RootResolutionError(
            code="invalid_root",
            message=f"worktree root symlink does not resolve to a directory: {root}",
            details={"root": str(root), "reason": "broken_or_non_directory_symlink"},
        )

    raise RootResolutionError(
        code="invalid_root",
        message=f"worktree root is not a directory: {root}",
        details={"root": str(root), "reason": kind},
    )


def validate_namespace(
    root: Path,
    repo_basename: str,
    filesystem: FilesystemGateway,
) -> Path:
    """Return a safe repository namespace, rejecting symlink/other entries."""

    namespace = root / repo_basename
    kind = filesystem.lstat_kind(namespace)
    if kind in {"missing", "directory"}:
        return namespace
    raise RootResolutionError(
        code="unsafe_namespace",
        message=f"managed namespace is not a real directory: {namespace}",
        details={"namespace": str(namespace), "reason": kind},
    )


def normalize_label(label: str | None) -> str | None:
    """Validate the optional lowercase label grammar."""

    if label is None:
        return None
    if not label or LABEL_PATTERN.fullmatch(label) is None:
        raise LabelValidationError("invalid worktree label: use lowercase letters, digits, and hyphens only")
    return label


def candidate_id(label: str | None, index: int) -> str:
    """Return ``wtN`` or ``labelN`` (with the first label unnumbered)."""

    if index < 1:
        raise ValueError("candidate index must be at least 1")
    normalized = normalize_label(label)
    if normalized is None:
        return f"wt{index}"
    return normalized if index == 1 else f"{normalized}{index}"


def candidate_index(label: str | None, value: str) -> int:
    """Recover the bounded-loop index from a generated candidate id."""

    normalized = normalize_label(label)
    if normalized is None:
        if not value.startswith("wt"):
            raise ValueError(f"not an automatic worktree id: {value}")
        suffix = value[2:]
    elif value == normalized:
        return 1
    elif value.startswith(normalized):
        suffix = value[len(normalized) :]
    else:
        raise ValueError(f"not a candidate for label {normalized}: {value}")
    if not suffix.isdigit() or int(suffix) < 1:
        raise ValueError(f"invalid generated worktree id: {value}")
    return int(suffix)


def make_candidate(
    *,
    label: str | None,
    index: int,
    namespace: Path,
    repo_basename: str,
    branch_prefix: str,
) -> WorktreeCandidate:
    """Build one candidate from the main namespace and invocation branch."""

    worktree_id = candidate_id(label, index)
    return WorktreeCandidate(
        id=worktree_id,
        path=namespace / f"{repo_basename}-{worktree_id}",
        branch=f"{branch_prefix}-{worktree_id}",
    )


def canonical_path(path: Path) -> Path:
    """Canonicalize an arbitrary path without requiring it to exist."""

    return path.expanduser().resolve(strict=False)


def is_retryable_git_collision(error: BaseException) -> bool:
    """Recognize only an adapter-provided, operation-specific collision.

    Human-readable Git output is deliberately not a retry signal.  In
    particular, generic phrases such as ``already exists`` also occur for
    ref-lock, permission, and I/O failures.  Concrete adapters must provide a
    typed ``collision_kind`` after their own fail-closed classification.
    """

    collision_kind = getattr(error, "collision_kind", None)
    return isinstance(collision_kind, str) and collision_kind in RETRYABLE_GIT_COLLISION_KINDS


def _canonical_root(root: Path) -> Path:
    try:
        return root.expanduser().resolve(strict=False)
    except OSError as exc:
        raise RootResolutionError(
            code="invalid_root",
            message=f"failed to resolve worktree root: {root}",
            details={"root": str(root), "reason": str(exc)},
        ) from exc


__all__ = [
    "LABEL_PATTERN",
    "MAX_CANDIDATE_ATTEMPTS",
    "RETRYABLE_GIT_COLLISION_KINDS",
    "ROOT_ENVIRONMENT",
    "LabelValidationError",
    "RootResolutionError",
    "WorktreeCandidate",
    "candidate_id",
    "candidate_index",
    "canonical_path",
    "is_retryable_git_collision",
    "make_candidate",
    "normalize_label",
    "select_root",
    "validate_namespace",
    "validate_root",
]

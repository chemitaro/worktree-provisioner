"""Application service for Git linked worktree lifecycle operations."""

from __future__ import annotations

import builtins
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from worktree_provisioner.application.contracts import (
    ArtifactState,
    BlockerCode,
    BootstrapResult,
    CreateRequest,
    CreateResult,
    ExpectedError,
    GitWorktreeRecord,
    ListRequest,
    ListResult,
    Operation,
    RemoveRequest,
    RemoveResult,
    ResultWarning,
    ShowRequest,
    ShowResult,
    WorktreeOrigin,
    WorktreeRecordView,
)
from worktree_provisioner.application.ports import ApplicationPorts
from worktree_provisioner.application.root_and_naming import (
    MAX_CANDIDATE_ATTEMPTS,
    LabelValidationError,
    RootResolutionError,
    WorktreeCandidate,
    candidate_index,
    canonical_path,
    is_retryable_git_collision,
    make_candidate,
    normalize_label,
    validate_namespace,
    validate_root,
)
from worktree_provisioner.application.target_resolver import TargetResolutionError, resolve_target

_DETECTION_COMMAND: Final[tuple[str, ...]] = ("make", "-n", "init")
_DIAGNOSTIC_LIMIT: Final[int] = 4096
_COLLISION_WARNING_CODE: Final[str] = "collision_partial_artifact"


@dataclass(frozen=True, slots=True)
class _NamespaceContext:
    namespace: Path
    available: bool
    reason: str
    exists: bool


class WorktreeService:
    """Application service implementing the four lifecycle operations."""

    def __init__(self, ports: ApplicationPorts) -> None:
        self.ports = ports

    def create(self, request: CreateRequest) -> CreateResult:
        """Create one linked worktree, retaining it on partial failures."""

        root = self._resolve_root(request.root)
        try:
            label = normalize_label(request.label)
        except LabelValidationError as exc:
            raise self._error(
                code="invalid_label",
                message=str(exc),
                details={"label": request.label},
            ) from exc

        current_branch = self._current_branch(request.repo_root)
        records = self._worktree_records(request.repo_root)
        if not records:
            raise self._error(
                code="git_worktree_list_failed",
                message="git worktree list returned no worktree records",
                details={},
            )

        main_worktree_path = canonical_path(_record_path(records[0]))
        repo_basename = main_worktree_path.name
        if not repo_basename:
            raise self._error(
                code="git_worktree_list_failed",
                message="main worktree record has no repository basename",
                details={"path": str(main_worktree_path)},
            )

        try:
            namespace = validate_namespace(root, repo_basename, self.ports.filesystem)
        except RootResolutionError as exc:
            raise self._error(code=exc.code, message=str(exc), details=exc.details) from exc

        candidate = self._select_candidate(
            request.repo_root,
            records,
            namespace=namespace,
            repo_basename=repo_basename,
            branch_prefix=current_branch,
            label=label,
        )

        self._ensure_create_directories(root, namespace, request.repo_root, candidate)
        # Recheck the namespace immediately before Git mutation.  The
        # filesystem adapter rejects a symlink even if it appeared after the
        # first validation, so no worktree can be redirected through it.
        try:
            validate_namespace(root, repo_basename, self.ports.filesystem)
        except RootResolutionError as exc:
            raise self._error(code=exc.code, message=str(exc), details=exc.details) from exc

        warnings: list[ResultWarning] = []
        try:
            self._add_worktree(request.repo_root, root=root, namespace=namespace, candidate=candidate)
        except Exception as exc:
            # Inspect the failed candidate before deciding whether to retry.
            # The artifact named by a typed collision is the expected race
            # collider (for example, a branch created between preflight and
            # Git).  Any additional candidate artifact remains fail-closed so
            # a partial mutation is never hidden by the retry.
            artifacts = self._observe_artifacts(request.repo_root, candidate, namespace)
            if is_retryable_git_collision(exc):
                refreshed = self._try_refresh_records(request.repo_root)
                if refreshed is not None and _collision_retry_is_safe(exc, artifacts, candidate, refreshed):
                    _append_collision_warning(warnings, candidate, artifacts)
                    # A recognised race is the only path allowed to retry.
                    # Preserve any created artifacts; the next attempt gets a
                    # fresh inventory snapshot and a new candidate.
                    return self._retry_after_collision(
                        request,
                        root=root,
                        label=label,
                        current_branch=current_branch,
                        main_worktree_path=main_worktree_path,
                        repo_basename=repo_basename,
                        namespace=namespace,
                        records=refreshed,
                        failed_candidate=candidate,
                        warnings=warnings,
                    )
            raise self._error(
                code="git_worktree_add_failed",
                message=f"git worktree add failed for {candidate.id}",
                details={
                    "attempted_id": candidate.id,
                    "attempted_path": str(candidate.path),
                    "attempted_branch": candidate.branch,
                    "artifacts": _artifact_dict(artifacts),
                    "diagnostic": _bounded(str(exc)),
                },
            ) from exc

        result = self._result_after_add(
            request,
            candidate=candidate,
            root=root,
            namespace=namespace,
            main_worktree_path=main_worktree_path,
        )
        if not request.bootstrap_enabled:
            return result

        bootstrap = self._run_bootstrap(candidate.path)
        artifacts = self._observe_artifacts(request.repo_root, candidate, namespace)
        result = CreateResult(
            id=result.id,
            main_worktree_path=result.main_worktree_path,
            container_path=result.container_path,
            worktree_path=result.worktree_path,
            branch=result.branch,
            bootstrap=bootstrap,
            artifacts=artifacts,
        )
        if bootstrap.status == "detection_failed":
            raise self._error(
                code="bootstrap_detection_failed",
                message="make init detection failed; worktree was retained",
                details={"bootstrap": _bootstrap_dict(bootstrap)},
                result=result,
                status="partial",
            )
        if bootstrap.status == "failed":
            raise self._error(
                code="bootstrap_failed",
                message="make init failed; worktree was retained",
                details={"bootstrap": _bootstrap_dict(bootstrap)},
                result=result,
                status="partial",
            )
        return result

    def list(self, request: ListRequest) -> ListResult:
        """Return the complete Git inventory with safety classification."""

        root = self._resolve_root(request.root, operation="list")
        records = self._worktree_records(request.repo_root, operation="list")
        return ListResult(self._inventory_views(request.repo_root, root, records, operation="list"))

    def show(self, request: ShowRequest) -> ShowResult:
        """Resolve one target from a fresh inventory snapshot."""

        listed = self.list(ListRequest(repo_root=request.repo_root, root=request.root))
        try:
            record = resolve_target(listed.worktrees, request.target)
        except TargetResolutionError as exc:
            raise self._error(
                code=exc.code,
                message=str(exc),
                details=exc.details,
                operation="show",
            ) from exc
        return ShowResult(target=request.target, worktree=record)

    def remove(self, request: RemoveRequest) -> RemoveResult:
        """Remove one managed worktree with a final fail-closed refresh.

        The initial inventory is only a candidate check.  The Git inventory is
        fetched again immediately before mutation and the original selector is
        resolved against that snapshot.  Filesystem cleanup is deliberately
        reachable only after Git reports success.
        """

        root = self._resolve_root(request.root, operation="remove")
        initial_records = self._worktree_records(request.repo_root, operation="remove")
        if not initial_records:
            raise self._error(
                code="git_worktree_list_failed",
                message="git worktree list returned no worktree records",
                details={},
                operation="remove",
            )
        repo_basename = _record_canonical_path(initial_records[0]).name
        if not repo_basename:
            raise self._error(
                code="git_worktree_list_failed",
                message="invocation repository has no basename",
                details={"repo_root": str(request.repo_root)},
                operation="remove",
            )

        namespace = self._validate_remove_namespace(root, repo_basename)
        initial_views = self._inventory_views(
            request.repo_root,
            root,
            initial_records,
            operation="remove",
        )
        initial = self._resolve_remove_target(initial_views, request.target, phase="initial", request=request)
        initial_main_worktree_path = _record_canonical_path(initial_records[0])
        initial_blockers = self._remove_blockers_for(
            initial,
            root=root,
            namespace=namespace,
            repo_root=request.repo_root,
            main_worktree_path=initial_main_worktree_path,
        )
        if initial_blockers:
            raise self._remove_blocked(request, initial, initial_blockers)

        # Refresh every Git record and rebuild classification using the same
        # root and repository namespace immediately before mutation.
        refreshed_namespace = self._validate_remove_namespace(root, repo_basename)
        refreshed_records = self._worktree_records(request.repo_root, operation="remove")
        if not refreshed_records:
            raise self._remove_blocked_without_target(
                request,
                blocker="record_missing_after_refresh",
                message="worktree record disappeared during remove refresh",
            )
        refreshed_views = self._inventory_views(
            request.repo_root,
            root,
            refreshed_records,
            operation="remove",
        )
        refreshed = self._resolve_remove_target(refreshed_views, request.target, phase="refresh", request=request)
        refreshed_main_worktree_path = _record_canonical_path(refreshed_records[0])
        if canonical_path(initial.path) != canonical_path(refreshed.path):
            raise self._remove_blocked(
                request,
                refreshed,
                ("target_changed_after_refresh",),
                message="worktree target changed during remove refresh",
            )
        if canonical_path(namespace) != canonical_path(refreshed_namespace):
            raise self._remove_blocked(
                request,
                refreshed,
                ("target_changed_after_refresh",),
                message="managed namespace changed during remove refresh",
            )

        refreshed_blockers = self._remove_blockers_for(
            refreshed,
            root=root,
            namespace=refreshed_namespace,
            repo_root=request.repo_root,
            main_worktree_path=refreshed_main_worktree_path,
        )
        if refreshed_blockers:
            raise self._remove_blocked(request, refreshed, refreshed_blockers)

        try:
            self._remove_worktree(
                request.repo_root,
                root=root,
                namespace=refreshed_namespace,
                path=refreshed.path,
                force=request.force,
            )
        except Exception as exc:
            # No filesystem cleanup may happen on a Git refusal/failure.
            raise self._error(
                code="git_worktree_remove_failed",
                message=f"git worktree remove failed for {refreshed.path}",
                details={
                    "target": request.target,
                    "path": str(refreshed.path),
                    "force_requested": request.force,
                    "diagnostic": _bounded(str(exc)),
                },
                operation="remove",
            ) from exc

        cleanup_error = self._cleanup_after_remove(
            request,
            root=root,
            namespace=refreshed_namespace,
            target=refreshed,
            main_worktree_path=refreshed_main_worktree_path,
        )
        if cleanup_error is not None:
            raise self._error(
                code="post_remove_cleanup_failed",
                message="Git worktree record was removed but target cleanup failed",
                details=cleanup_error,
                result=RemoveResult(
                    target=request.target,
                    resolved_target=refreshed,
                    force_requested=request.force,
                    removed_record=True,
                    removed_directory=False,
                    branch_deleted=False,
                ),
                status="partial",
                operation="remove",
            )

        return RemoveResult(
            target=request.target,
            resolved_target=refreshed,
            force_requested=request.force,
            removed_record=True,
            removed_directory=True,
            branch_deleted=False,
        )

    def _validate_remove_namespace(self, root: Path, repo_basename: str) -> Path:
        """Validate a remove namespace before inventory or mutation."""

        namespace = root / repo_basename
        try:
            kind = self.ports.filesystem.lstat_kind(namespace)
        except Exception as exc:
            raise self._error(
                code="unsafe_namespace",
                message=f"failed to inspect managed namespace: {namespace}",
                details={"namespace": str(namespace), "diagnostic": _bounded(str(exc))},
                operation="remove",
            ) from exc
        if kind in {"missing", "directory"}:
            return namespace
        raise self._error(
            code="unsafe_namespace",
            message=f"managed namespace is not a real directory: {namespace}",
            details={"namespace": str(namespace), "reason": kind},
            operation="remove",
        )

    def _resolve_remove_target(
        self,
        records: Sequence[WorktreeRecordView],
        target: str,
        *,
        phase: str,
        request: RemoveRequest,
    ) -> WorktreeRecordView:
        try:
            return resolve_target(records, target)
        except TargetResolutionError as exc:
            if phase == "initial":
                raise self._error(
                    code=exc.code,
                    message=str(exc),
                    details=exc.details,
                    operation="remove",
                ) from exc
            blocker: BlockerCode
            if exc.code == "target_not_found":
                blocker = "record_missing_after_refresh"
                message = "worktree record disappeared during remove refresh"
            else:
                blocker = "target_changed_after_refresh"
                message = "worktree target could not be re-resolved during remove refresh"
            raise self._remove_blocked_without_target(
                request,
                blocker=blocker,
                message=message,
                details={"resolution_code": exc.code, **exc.details},
            ) from exc

    def _remove_blockers_for(
        self,
        record: WorktreeRecordView,
        *,
        root: Path,
        namespace: Path,
        repo_root: Path,
        main_worktree_path: Path,
    ) -> tuple[BlockerCode, ...]:
        blockers = list(record.remove_blockers)
        if self._is_protected_cleanup_path(record.path, root, namespace, repo_root, main_worktree_path):
            blockers.append("protected_cleanup_path")
        # Inventory classification is intentionally repeated at the remove
        # boundary; a malformed view must fail closed rather than rely on a
        # presentation-layer ``managed`` flag.
        if not self._is_managed_path(record.path, namespace) and "outside_managed_namespace" not in blockers:
            blockers.append("outside_managed_namespace")
        return tuple(dict.fromkeys(blockers))

    def _is_protected_cleanup_path(
        self,
        target: Path,
        root: Path,
        namespace: Path,
        repo_root: Path,
        main_worktree_path: Path,
    ) -> bool:
        lexical_target = _absolute_lexical_path(target)
        try:
            canonical_target = canonical_path(target)
        except (OSError, RuntimeError):
            return True
        for protected in (root, namespace, repo_root, main_worktree_path):
            lexical_protected = _absolute_lexical_path(protected)
            try:
                canonical_protected = canonical_path(protected)
            except (OSError, RuntimeError):
                return True
            lexical_target_is_protected = (
                lexical_target == lexical_protected or lexical_target in lexical_protected.parents
            )
            canonical_target_is_protected = (
                canonical_target == canonical_protected or canonical_target in canonical_protected.parents
            )
            if lexical_target_is_protected or canonical_target_is_protected:
                return True
        return False

    def _remove_blocked(
        self,
        request: RemoveRequest,
        record: WorktreeRecordView,
        blockers: Sequence[BlockerCode],
        *,
        message: str = "worktree removal is blocked by safety policy",
    ) -> ExpectedError:
        return self._error(
            code="remove_blocked",
            message=message,
            details={
                "target": request.target,
                "worktree": record,
                "resolved_target": record,
                "remove_blockers": list(dict.fromkeys(blockers)),
                "force_requested": request.force,
            },
            operation="remove",
        )

    def _remove_blocked_without_target(
        self,
        request: RemoveRequest,
        *,
        blocker: BlockerCode,
        message: str,
        details: Mapping[str, object] | None = None,
    ) -> ExpectedError:
        payload: dict[str, object] = {
            "target": request.target,
            "remove_blockers": [blocker],
            "force_requested": request.force,
        }
        if details:
            payload.update(details)
        return self._error(
            code="remove_blocked",
            message=message,
            details=payload,
            operation="remove",
        )

    def _cleanup_after_remove(
        self,
        request: RemoveRequest,
        *,
        root: Path,
        namespace: Path,
        target: WorktreeRecordView,
        main_worktree_path: Path,
    ) -> dict[str, object] | None:
        """Recheck containment and clean only the exact leftover target."""

        try:
            kind = self.ports.filesystem.lstat_kind(namespace)
        except Exception as exc:
            return {"reason": "namespace_recheck_failed", "diagnostic": _bounded(str(exc))}
        if kind != "directory":
            return {"reason": "unsafe_namespace_after_git", "namespace": str(namespace), "kind": kind}
        if not self._is_managed_path(target.path, namespace) or self._is_protected_cleanup_path(
            target.path, root, namespace, request.repo_root, main_worktree_path
        ):
            return {
                "reason": "protected_or_outside_target_after_git",
                "path": str(target.path),
            }
        try:
            exists = self.ports.filesystem.path_exists_no_follow(target.path)
        except Exception as exc:
            return {"reason": "target_inspection_failed", "path": str(target.path), "diagnostic": _bounded(str(exc))}
        if not exists:
            return None
        try:
            self._remove_target(namespace, target.path, root=root)
        except Exception as exc:
            # A target that disappeared between the existence check and the
            # cleanup adapter is already clean.  Other races and permissions
            # remain observable partial results.
            if getattr(exc, "kind", None) == "missing":
                return None
            return {
                "reason": "target_cleanup_failed",
                "path": str(target.path),
                "diagnostic": _bounded(str(exc)),
                "kind": getattr(exc, "kind", None),
            }
        return None

    def _remove_target(self, namespace: Path, target: Path, *, root: Path | None = None) -> None:
        """Remove a leftover target through a namespace-bound capability."""

        open_directory = getattr(self.ports.filesystem, "open_directory", None)
        remove_bound = getattr(self.ports.filesystem, "remove_target_no_follow_bound", None)
        if callable(open_directory) and callable(remove_bound):
            if root is None:
                with open_directory(namespace) as directory:
                    remove_bound(directory, target)
            else:
                with open_directory(root) as root_directory, open_directory(namespace) as directory:
                    bind_root = getattr(directory, "bind_root", None)
                    if callable(bind_root):
                        bind_root(root_directory)
                    remove_bound(directory, target)
            return
        self.ports.filesystem.remove_target_no_follow(target)

    def _remove_worktree(
        self,
        repo_root: Path,
        *,
        root: Path,
        namespace: Path,
        path: Path,
        force: bool,
    ) -> None:
        """Remove a managed worktree through a root-bound namespace fd."""

        open_directory = getattr(self.ports.filesystem, "open_directory", None)
        remove_bound = getattr(self.ports.git, "remove_worktree_bound", None)
        if callable(open_directory) and callable(remove_bound):
            lexical_path = _absolute_lexical_path(path)
            lexical_namespace = _absolute_lexical_path(namespace)
            try:
                relative = lexical_path.relative_to(lexical_namespace)
            except ValueError as exc:
                raise RuntimeError("managed worktree path is not a strict namespace descendant") from exc
            if not relative.parts:
                raise RuntimeError("managed worktree path is not a strict namespace descendant")
            relative_name = relative.as_posix()
            with open_directory(root) as root_directory, open_directory(namespace) as directory:
                bind_root = getattr(directory, "bind_root", None)
                if callable(bind_root):
                    bind_root(root_directory)
                remove_bound(
                    repo_root,
                    directory=directory,
                    name=relative_name,
                    force=force,
                )
            return
        self.ports.git.remove_worktree(repo_root, path=path, force=force)

    def _inventory_views(
        self,
        repo_root: Path,
        root: Path,
        records: Sequence[GitWorktreeRecord],
        *,
        operation: Operation,
    ) -> tuple[WorktreeRecordView, ...]:
        if not records:
            return ()

        main_path = _record_canonical_path(records[0])
        repo_basename = main_path.name
        if not repo_basename:
            raise self._error(
                code="git_worktree_list_failed",
                message="main worktree record has no repository basename",
                details={"path": str(main_path)},
                operation=operation,
            )
        namespace = self._namespace_context(root, repo_basename, operation=operation)
        current_path = canonical_path(repo_root)

        provisional: list[tuple[GitWorktreeRecord, bool, bool, bool, str, str, bool, bool]] = []
        for index, record in enumerate(records):
            path = _record_path(record)
            canonical_record_path = _record_canonical_path(record)
            main = index == 0
            current = canonical_record_path == current_path
            path_exists = _observe_path(self.ports.filesystem, path)
            managed = namespace.available and namespace.exists and self._is_managed_path(path, namespace.namespace)
            raw_id = self._raw_inventory_id(
                record,
                is_main=main,
                managed=managed,
                repo_basename=repo_basename,
            )
            provisional.append(
                (
                    record,
                    main,
                    current,
                    path_exists is True,
                    raw_id,
                    canonical_record_path.as_posix(),
                    managed,
                    namespace.available,
                )
            )

        stable_ids = _disambiguate_ids(provisional)
        views: list[WorktreeRecordView] = []
        for index, (record, main, current, path_exists, _raw_id, _sort_path, managed, available) in enumerate(
            provisional
        ):
            blockers = _remove_blockers(
                main=main,
                current=current,
                bare=record.bare,
                locked=record.locked,
                path_exists=path_exists,
                managed=managed,
                classification_available=available,
            )
            origin: WorktreeOrigin
            if not available:
                origin = "classification_unavailable"
            elif managed:
                origin = "managed_namespace"
            else:
                origin = "external"
            views.append(
                WorktreeRecordView(
                    id=stable_ids[index],
                    path=_absolute_lexical_path(_record_path(record)),
                    basename=_record_path(record).name,
                    branch=record.branch,
                    head=record.head,
                    detached=record.detached,
                    bare=record.bare,
                    locked=record.locked,
                    lock_reason=record.lock_reason,
                    main=main,
                    current=current,
                    path_exists=path_exists,
                    record_exists=True,
                    managed=managed,
                    classification_available=available,
                    classification_reason="root_valid" if available else "namespace_symlink",
                    origin=origin,
                    removable=not blockers,
                    remove_blockers=blockers,
                )
            )
        return tuple(views)

    def _namespace_context(self, root: Path, repo_basename: str, *, operation: Operation) -> _NamespaceContext:
        namespace = root / repo_basename
        try:
            kind = self.ports.filesystem.lstat_kind(namespace)
        except Exception as exc:
            raise self._error(
                code="unsafe_namespace",
                message=f"failed to inspect managed namespace: {namespace}",
                details={"namespace": str(namespace), "diagnostic": _bounded(str(exc))},
                operation=operation,
            ) from exc
        if kind == "symlink":
            return _NamespaceContext(namespace=namespace, available=False, reason="namespace_symlink", exists=False)
        if kind == "missing":
            return _NamespaceContext(namespace=namespace, available=True, reason="root_valid", exists=False)
        if kind == "directory":
            return _NamespaceContext(namespace=namespace, available=True, reason="root_valid", exists=True)
        raise self._error(
            code="unsafe_namespace",
            message=f"managed namespace is not a real directory: {namespace}",
            details={"namespace": str(namespace), "reason": kind},
            operation=operation,
        )

    def _is_managed_path(self, path: Path, namespace: Path) -> bool:
        lexical_path = _absolute_lexical_path(path)
        lexical_namespace = _absolute_lexical_path(namespace)
        if not _is_strict_descendant(lexical_path, lexical_namespace):
            return False
        try:
            canonical_namespace = canonical_path(namespace)
            canonical_record = canonical_path(path)
        except (OSError, RuntimeError):
            return False
        if not _is_strict_descendant(canonical_record, canonical_namespace):
            if not self._is_broken_final_symlink(path):
                return False
            # A broken final symlink has no canonical target to contain.  Its
            # lexical entry is still strictly inside the namespace and can be
            # safely unlinked without following it.
            canonical_record = lexical_path
        try:
            relative = lexical_path.relative_to(lexical_namespace)
        except ValueError:
            return False
        current = lexical_namespace
        for index, part in enumerate(relative.parts):
            current /= part
            try:
                kind = self.ports.filesystem.lstat_kind(current)
            except Exception:
                return False
            # A symlink in the namespace ancestry can redirect a whole
            # subtree.  The final target itself is allowed: post-Git cleanup
            # explicitly removes that link without following it.
            if kind == "symlink" and index != len(relative.parts) - 1:
                return False
            if kind == "missing":
                # A stale Git record may have no path at all.  No existing
                # component can redirect it once the canonical check passed.
                break
            if kind == "other" and index != len(relative.parts) - 1:
                return False
        return True

    def _is_broken_final_symlink(self, path: Path) -> bool:
        try:
            if self.ports.filesystem.lstat_kind(path) != "symlink":
                return False
            path.resolve(strict=True)
        except FileNotFoundError:
            return True
        except (OSError, RuntimeError):
            return False
        return False

    @staticmethod
    def _raw_inventory_id(
        record: GitWorktreeRecord,
        *,
        is_main: bool,
        managed: bool,
        repo_basename: str,
    ) -> str:
        if is_main:
            return "main"
        basename = record.path.name or "worktree"
        if managed and basename.startswith(f"{repo_basename}-"):
            suffix = basename[len(repo_basename) + 1 :]
            if suffix:
                return suffix
        return basename

    def _resolve_root(self, root: Path, *, operation: Operation = "create") -> Path:
        try:
            return validate_root(root, self.ports.filesystem, allow_missing=True)
        except RootResolutionError as exc:
            raise self._error(code=exc.code, message=str(exc), details=exc.details, operation=operation) from exc
        except Exception as exc:
            raise self._error(
                code="invalid_root",
                message=f"failed to inspect worktree root: {root}",
                details={"root": str(root), "diagnostic": _bounded(str(exc))},
                operation=operation,
            ) from exc

    def _current_branch(self, repo_root: Path) -> str:
        try:
            branch = self.ports.git.current_branch_or_none(repo_root)
        except Exception as exc:
            raise self._error(
                code=_git_repository_error_code(exc),
                message="failed to resolve the invocation checkout branch",
                details={"diagnostic": _bounded(str(exc))},
            ) from exc
        if branch is None or not branch.strip():
            raise self._error(
                code="detached_head",
                message="worktree create requires a named current branch; detached HEAD is not supported",
                details={},
            )
        return branch

    def _worktree_records(
        self, repo_root: Path, *, operation: Operation = "create"
    ) -> builtins.list[GitWorktreeRecord]:
        try:
            return list(self.ports.git.worktree_list(repo_root))
        except Exception as exc:
            raise self._error(
                code="git_worktree_list_failed",
                message="failed to list Git worktrees",
                details={"diagnostic": _bounded(str(exc))},
                operation=operation,
            ) from exc

    def _select_candidate(
        self,
        repo_root: Path,
        records: Sequence[GitWorktreeRecord],
        *,
        namespace: Path,
        repo_basename: str,
        branch_prefix: str,
        label: str | None,
    ) -> WorktreeCandidate:
        known_paths = {_record_canonical_path(record) for record in records}
        last: WorktreeCandidate | None = None
        last_reason = "no candidates attempted"
        for index in range(1, MAX_CANDIDATE_ATTEMPTS + 1):
            candidate = make_candidate(
                label=label,
                index=index,
                namespace=namespace,
                repo_basename=repo_basename,
                branch_prefix=branch_prefix,
            )
            last = candidate
            collision = self._preflight_collision(repo_root, candidate, known_paths)
            if collision is None:
                return candidate
            last_reason = collision

        assert last is not None
        raise self._error(
            code="candidate_exhausted",
            message="worktree create exhausted candidate attempts",
            details={
                "attempts": MAX_CANDIDATE_ATTEMPTS,
                "last_id": last.id,
                "last_path": str(last.path),
                "last_branch": last.branch,
                "reason": last_reason,
            },
        )

    def _preflight_collision(
        self,
        repo_root: Path,
        candidate: WorktreeCandidate,
        known_paths: set[Path],
    ) -> str | None:
        if canonical_path(candidate.path) in known_paths:
            return "worktree record already exists"
        try:
            if self.ports.filesystem.path_exists_no_follow(candidate.path):
                return "worktree path already exists"
        except Exception as exc:
            raise self._error(
                code="git_worktree_add_failed",
                message=f"failed to inspect candidate path: {candidate.path}",
                details={"attempted_path": str(candidate.path), "diagnostic": _bounded(str(exc))},
            ) from exc

        try:
            if self.ports.git.local_branch_exists(repo_root, candidate.branch):
                return "branch already exists"
            if not self.ports.git.check_branch_ref(repo_root, candidate.branch):
                raise self._error(
                    code="git_worktree_add_failed",
                    message=f"generated worktree branch is invalid: {candidate.branch}",
                    details={
                        "attempted_id": candidate.id,
                        "attempted_path": str(candidate.path),
                        "attempted_branch": candidate.branch,
                        "reason": "invalid_generated_branch",
                    },
                )
        except ExpectedError:
            raise
        except Exception as exc:
            raise self._error(
                code="git_worktree_add_failed",
                message=f"failed to inspect candidate branch: {candidate.branch}",
                details={"attempted_branch": candidate.branch, "diagnostic": _bounded(str(exc))},
            ) from exc
        return None

    def _ensure_create_directories(
        self,
        root: Path,
        namespace: Path,
        repo_root: Path,
        candidate: WorktreeCandidate,
    ) -> None:
        try:
            self.ports.filesystem.ensure_directory(root)
            self.ports.filesystem.ensure_directory(namespace)
        except Exception as exc:
            artifacts = self._observe_artifacts(repo_root, candidate, namespace)
            raise self._error(
                code="container_create_failed",
                message=f"failed to create worktree container: {namespace}",
                details={
                    "container_path": str(namespace),
                    "artifacts": _artifact_dict(artifacts),
                    "diagnostic": _bounded(str(exc)),
                },
            ) from exc

    def _add_worktree(
        self,
        repo_root: Path,
        *,
        root: Path,
        namespace: Path,
        candidate: WorktreeCandidate,
    ) -> None:
        """Use the descriptor-bound adapter path when available."""

        open_directory = getattr(self.ports.filesystem, "open_directory", None)
        add_worktree_bound = getattr(self.ports.git, "add_worktree_bound", None)
        if callable(open_directory) and callable(add_worktree_bound):
            with open_directory(root) as root_directory, open_directory(namespace) as directory:
                bind_root = getattr(directory, "bind_root", None)
                if callable(bind_root):
                    bind_root(root_directory)
                add_worktree_bound(
                    repo_root,
                    directory=directory,
                    name=candidate.path.name,
                    branch=candidate.branch,
                )
            return
        # Test doubles and third-party ports that implement the original
        # narrow protocol keep the existing fixed Git operation semantics.
        self.ports.git.add_worktree(repo_root, path=candidate.path, branch=candidate.branch)

    def _result_after_add(
        self,
        request: CreateRequest,
        *,
        candidate: WorktreeCandidate,
        root: Path,
        namespace: Path,
        main_worktree_path: Path,
        warnings: Sequence[ResultWarning] = (),
    ) -> CreateResult:
        artifacts = self._observe_artifacts(request.repo_root, candidate, namespace)
        disabled = BootstrapResult(
            requested=False,
            status="disabled",
            command=None,
            exit_code=None,
            detail=None,
        )
        return CreateResult(
            id=candidate.id,
            main_worktree_path=main_worktree_path,
            container_path=canonical_path(namespace),
            worktree_path=canonical_path(candidate.path),
            branch=candidate.branch,
            bootstrap=disabled,
            artifacts=artifacts,
            warnings=tuple(warnings),
        )

    def _run_bootstrap(self, worktree_path: Path) -> BootstrapResult:
        try:
            raw = self.ports.bootstrap.run_make_init_if_available(worktree_path)
        except Exception as exc:
            return BootstrapResult(
                requested=True,
                status="detection_failed",
                command=_DETECTION_COMMAND,
                exit_code=None,
                detail=_bounded(str(exc)),
            )
        return _coerce_bootstrap_result(raw)

    def _observe_artifacts(
        self,
        repo_root: Path,
        candidate: WorktreeCandidate,
        namespace: Path,
    ) -> ArtifactState:
        container_exists = _observe_path(self.ports.filesystem, namespace)
        worktree_path_exists = _observe_path(self.ports.filesystem, candidate.path)
        try:
            branch_exists: bool | None = self.ports.git.local_branch_exists(repo_root, candidate.branch)
        except Exception:
            branch_exists = None
        try:
            records = self.ports.git.worktree_list(repo_root)
        except Exception:
            record_exists = None
        else:
            target = canonical_path(candidate.path)
            record_exists = any(_record_canonical_path(record) == target for record in records)
        return ArtifactState(container_exists, worktree_path_exists, branch_exists, record_exists)

    def _try_refresh_records(self, repo_root: Path) -> builtins.list[GitWorktreeRecord] | None:
        try:
            return list(self.ports.git.worktree_list(repo_root))
        except Exception:
            return None

    def _retry_after_collision(
        self,
        request: CreateRequest,
        *,
        root: Path,
        label: str | None,
        current_branch: str,
        main_worktree_path: Path,
        repo_basename: str,
        namespace: Path,
        records: builtins.list[GitWorktreeRecord],
        failed_candidate: WorktreeCandidate,
        warnings: builtins.list[ResultWarning],
    ) -> CreateResult:
        """Continue a recognised add collision without recursive call growth."""

        known_paths = {_record_canonical_path(record) for record in records}
        try:
            start_index = candidate_index(label, failed_candidate.id) + 1
        except ValueError:
            start_index = 1
        for index in range(start_index, MAX_CANDIDATE_ATTEMPTS + 1):
            candidate = make_candidate(
                label=label,
                index=index,
                namespace=namespace,
                repo_basename=repo_basename,
                branch_prefix=current_branch,
            )
            # The failed candidate may have been rejected by Git before the
            # refresh exposed its record; explicitly skip it once.
            if candidate.id == failed_candidate.id:
                continue
            try:
                collision = self._preflight_collision(request.repo_root, candidate, known_paths)
            except ExpectedError as exc:
                raise self._with_warnings(exc, warnings) from exc
            if collision is not None:
                continue
            try:
                self._ensure_create_directories(root, namespace, request.repo_root, candidate)
            except ExpectedError as exc:
                raise self._with_warnings(exc, warnings) from exc
            try:
                validate_namespace(root, repo_basename, self.ports.filesystem)
            except RootResolutionError as exc:
                raise self._error(
                    code=exc.code,
                    message=str(exc),
                    details=exc.details,
                    warnings=warnings,
                ) from exc
            try:
                self._add_worktree(request.repo_root, root=root, namespace=namespace, candidate=candidate)
            except Exception as exc:
                artifacts = self._observe_artifacts(request.repo_root, candidate, namespace)
                if is_retryable_git_collision(exc):
                    refreshed = self._try_refresh_records(request.repo_root)
                    if refreshed is not None and _collision_retry_is_safe(exc, artifacts, candidate, refreshed):
                        _append_collision_warning(warnings, candidate, artifacts)
                        known_paths = {_record_canonical_path(record) for record in refreshed}
                        continue
                # A non-retryable terminal failure must still expose every
                # partial artifact retained by an earlier collision attempt.
                _append_collision_warning(warnings, candidate, artifacts)
                raise self._error(
                    code="git_worktree_add_failed",
                    message=f"git worktree add failed for {candidate.id}",
                    details={
                        "attempted_id": candidate.id,
                        "attempted_path": str(candidate.path),
                        "attempted_branch": candidate.branch,
                        "artifacts": _artifact_dict(artifacts),
                        "diagnostic": _bounded(str(exc)),
                    },
                    warnings=warnings,
                ) from exc
            result = self._result_after_add(
                request,
                candidate=candidate,
                root=root,
                namespace=namespace,
                main_worktree_path=main_worktree_path,
                warnings=warnings,
            )
            if not request.bootstrap_enabled:
                return result
            bootstrap = self._run_bootstrap(candidate.path)
            artifacts = self._observe_artifacts(request.repo_root, candidate, namespace)
            result = CreateResult(
                id=result.id,
                main_worktree_path=result.main_worktree_path,
                container_path=result.container_path,
                worktree_path=result.worktree_path,
                branch=result.branch,
                bootstrap=bootstrap,
                artifacts=artifacts,
                warnings=result.warnings,
            )
            if bootstrap.status == "detection_failed":
                raise self._error(
                    code="bootstrap_detection_failed",
                    message="make init detection failed; worktree was retained",
                    details={"bootstrap": _bootstrap_dict(bootstrap)},
                    result=result,
                    status="partial",
                    warnings=warnings,
                )
            if bootstrap.status == "failed":
                raise self._error(
                    code="bootstrap_failed",
                    message="make init failed; worktree was retained",
                    details={"bootstrap": _bootstrap_dict(bootstrap)},
                    result=result,
                    status="partial",
                    warnings=warnings,
                )
            return result

        raise self._error(
            code="candidate_exhausted",
            message="worktree create exhausted candidate attempts",
            details={"attempts": MAX_CANDIDATE_ATTEMPTS},
            warnings=warnings,
        )

    def _with_warnings(
        self,
        error: ExpectedError,
        warnings: Sequence[ResultWarning],
    ) -> ExpectedError:
        """Carry retry history through an unrelated terminal application error."""

        if not warnings:
            return error
        return ExpectedError(
            code=error.code,
            operation=error.operation,
            message=error.message,
            details=error.details,
            result=error.result,
            status=error.status,
            warnings=tuple(warnings) + error.warnings,
        )

    def _error(
        self,
        *,
        code: str,
        message: str,
        details: Mapping[str, object],
        result: object | None = None,
        status: str = "error",
        operation: Operation = "create",
        warnings: Sequence[ResultWarning] = (),
    ) -> ExpectedError:
        # The typed Literal contract is intentionally enforced at the shared
        # boundary; local adapter diagnostics cannot invent a public status.
        response_status = "partial" if status == "partial" else "error"
        return ExpectedError(
            code=code,  # type: ignore[arg-type]
            operation=operation,
            message=message,
            details=details,
            result=result,
            status=response_status,  # type: ignore[arg-type]
            warnings=tuple(warnings),
        )


def create_worktree(request: CreateRequest, ports: ApplicationPorts) -> CreateResult:
    """Functional entry point kept beside :class:`WorktreeService`."""

    return WorktreeService(ports).create(request)


def _record_path(record: object) -> Path:
    path = getattr(record, "path", None)
    if not isinstance(path, Path):
        raise ValueError("Git worktree record path must be a pathlib.Path")
    return path


def _record_canonical_path(record: object) -> Path:
    return canonical_path(_record_path(record))


def _absolute_lexical_path(path: Path) -> Path:
    """Make an absolute path without resolving symlink components."""

    return Path(os.path.abspath(str(path)))


def _is_strict_descendant(path: Path, parent: Path) -> bool:
    return path != parent and parent in path.parents


def _disambiguate_ids(
    provisional: Sequence[tuple[GitWorktreeRecord, bool, bool, bool, str, str, bool, bool]],
) -> list[str]:
    """Suffix duplicate raw ids by canonical path order, preserving list order."""

    groups: dict[str, list[tuple[int, str]]] = {}
    assigned: list[str | None] = [None] * len(provisional)
    used: set[str] = set()
    # The first Git record is the actual main worktree and must retain the
    # reserved selector ``main`` even if another record's basename is also
    # ``main`` and would sort before it lexically.
    for index, item in enumerate(provisional):
        if item[1]:
            assigned[index] = "main"
            used.add("main")
            continue
        groups.setdefault(item[4], []).append((index, item[5]))
    for raw_id in sorted(groups):
        ordered = sorted(groups[raw_id], key=lambda value: value[1])
        for ordinal, (index, _sort_path) in enumerate(ordered, start=1):
            candidate = raw_id if ordinal == 1 else f"{raw_id}~{ordinal}"
            suffix = ordinal
            while candidate in used:
                suffix += 1
                candidate = f"{raw_id}~{suffix}"
            used.add(candidate)
            assigned[index] = candidate
    return [value if value is not None else "worktree" for value in assigned]


def _remove_blockers(
    *,
    main: bool,
    current: bool,
    bare: bool,
    locked: bool,
    path_exists: bool,
    managed: bool,
    classification_available: bool,
) -> tuple[BlockerCode, ...]:
    blockers: list[BlockerCode] = []
    if main:
        blockers.append("main_worktree")
    if current:
        blockers.append("current_worktree")
    if bare:
        blockers.append("bare_worktree")
    if locked:
        blockers.append("locked_worktree")
    if not path_exists:
        blockers.append("path_missing")
    if classification_available:
        if not managed:
            blockers.append("outside_managed_namespace")
    else:
        blockers.append("classification_unavailable")
    return tuple(blockers)


def _observe_path(filesystem: object, path: Path) -> bool | None:
    try:
        return bool(filesystem.path_exists_no_follow(path))  # type: ignore[attr-defined]
    except Exception:
        return None


def _coerce_bootstrap_result(raw: object) -> BootstrapResult:
    status = getattr(raw, "status", "detection_failed")
    command_value = getattr(raw, "command", None)
    exit_code = getattr(raw, "exit_code", None)
    detail = getattr(raw, "detail", None)
    if not isinstance(detail, str) and detail is not None:
        detail = str(detail)
    if status == "disabled":
        return BootstrapResult(False, "disabled", None, None, detail)
    if status == "skipped":
        return BootstrapResult(True, "skipped", None, None, detail)
    if status == "succeeded":
        command = _command_tuple(command_value, ("make", "init"))
        return BootstrapResult(True, "succeeded", command, 0, detail)
    if status == "failed":
        command = _command_tuple(command_value, ("make", "init"))
        nonzero = exit_code if isinstance(exit_code, int) and exit_code != 0 else 1
        return BootstrapResult(True, "failed", command, nonzero, detail)
    command = _command_tuple(command_value, _DETECTION_COMMAND)
    return BootstrapResult(True, "detection_failed", command, exit_code if isinstance(exit_code, int) else None, detail)


def _command_tuple(value: object, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
        return value
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    if isinstance(value, str) and value:
        return tuple(value.split())
    return fallback


def _bootstrap_dict(result: BootstrapResult) -> dict[str, object]:
    return {
        "requested": result.requested,
        "status": result.status,
        "command": list(result.command) if result.command is not None else None,
        "exit_code": result.exit_code,
        "detail": result.detail,
    }


def _artifact_dict(result: ArtifactState) -> dict[str, bool | None]:
    return {
        "container_exists": result.container_exists,
        "worktree_path_exists": result.worktree_path_exists,
        "branch_exists": result.branch_exists,
        "worktree_record_exists": result.worktree_record_exists,
    }


def _append_collision_warning(
    warnings: builtins.list[ResultWarning],
    candidate: WorktreeCandidate,
    artifacts: ArtifactState,
) -> None:
    """Expose every retained collision artifact without unbounded text."""

    if not any(
        value is True
        for value in (
            artifacts.worktree_path_exists,
            artifacts.branch_exists,
            artifacts.worktree_record_exists,
        )
    ):
        return
    message = (
        "retry retained candidate artifacts: "
        f"id={_bounded(candidate.id, limit=128)} "
        f"branch={_bounded(candidate.branch, limit=256)} "
        f"path={_bounded(str(candidate.path), limit=1024)} "
        f"path_exists={artifacts.worktree_path_exists} "
        f"branch_exists={artifacts.branch_exists} "
        f"worktree_record_exists={artifacts.worktree_record_exists}"
    )
    warnings.append(
        ResultWarning(
            code=_COLLISION_WARNING_CODE,
            message=_bounded(message),
            facts={
                "candidate_id": _bounded(candidate.id, limit=128),
                "branch": _bounded(candidate.branch, limit=256),
                "path": _bounded(str(candidate.path), limit=1024),
                "path_exists": artifacts.worktree_path_exists,
                "branch_exists": artifacts.branch_exists,
                "worktree_record_exists": artifacts.worktree_record_exists,
            },
        )
    )


def _collision_retry_is_safe(
    error: BaseException,
    artifacts: ArtifactState,
    candidate: WorktreeCandidate,
    records: Sequence[GitWorktreeRecord],
) -> bool:
    """Allow a typed race retry without concealing a partial mutation.

    Git's typed collision is authoritative about which candidate namespace was
    refused.  The refreshed inventory then proves whether that namespace is a
    pre-existing collider or whether this attempt also created a candidate
    artifact.  Unknown/mismatched observations remain fail-closed.
    """

    kind = getattr(error, "collision_kind", None)
    if kind not in {"branch", "path", "checked_out"}:
        return False
    candidate_path = canonical_path(candidate.path)
    candidate_record = any(_record_canonical_path(record) == candidate_path for record in records)
    matching_branch_record = any(record.branch == candidate.branch for record in records)
    if candidate_record:
        return False

    if kind == "branch":
        # A local branch may have appeared in the race, but a worktree record
        # for it at another path is the expected checked-out collider.  The
        # candidate path and candidate record are the attempt-mutation gate.
        return (
            artifacts.worktree_path_exists is False
            and artifacts.branch_exists is True
            and artifacts.worktree_record_exists is False
        ) or (
            artifacts.worktree_path_exists is False
            and artifacts.branch_exists is False
            and artifacts.worktree_record_exists is False
        )

    if kind == "path":
        # The path itself is the expected collider.  A branch or worktree
        # record in addition to it indicates an attempt-derived mutation.
        # Git's ``worktree add -b`` creates the branch before it checks the
        # target directory.  A path race therefore legitimately leaves the
        # candidate branch behind even though no worktree record exists.  The
        # typed path collision and the absence of a matching record keep this
        # narrow; the branch is deliberately retained (no rollback).
        return (
            (
                artifacts.worktree_path_exists is True
                and artifacts.branch_exists is False
                and artifacts.worktree_record_exists is False
                and not matching_branch_record
            )
            or (
                artifacts.worktree_path_exists is True
                and artifacts.branch_exists is True
                and artifacts.worktree_record_exists is False
                and not matching_branch_record
            )
            or (
                artifacts.worktree_path_exists is False
                and artifacts.branch_exists is False
                and artifacts.worktree_record_exists is False
                and not matching_branch_record
            )
        )

    # A checked-out collision is proven by the same branch being recorded at a
    # different path.  The candidate path must remain absent.
    return (
        artifacts.worktree_path_exists is False
        and artifacts.branch_exists is True
        and artifacts.worktree_record_exists is False
        and any(
            record.branch == candidate.branch and _record_canonical_path(record) != candidate_path for record in records
        )
    )


def _git_repository_error_code(error: BaseException) -> str:
    text = str(error).lower()
    if "not found" in text or "executable" in text or "start git" in text:
        return "git_unavailable"
    return "repository_unavailable"


def _bounded(value: str, limit: int = _DIAGNOSTIC_LIMIT) -> str:
    if len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return f"{value[: limit - 1]}…"


__all__ = ["WorktreeService", "create_worktree"]

"""Create application service for Git linked worktrees.

This phase owns the create vertical slice only.  Inventory, target
resolution, and removal are intentionally left to later phase owners; the
service nevertheless keeps all create-side state in the shared P3 contracts
so the later command family can consume it without a compatibility shim.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from worktree_provisioner.application.contracts import (
    ArtifactState,
    BootstrapResult,
    CreateRequest,
    CreateResult,
    ExpectedError,
    GitWorktreeRecord,
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

_DETECTION_COMMAND: Final[tuple[str, ...]] = ("make", "-n", "init")
_DIAGNOSTIC_LIMIT: Final[int] = 4096


class WorktreeService:
    """Application service implementing create and bootstrap semantics."""

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

        try:
            self.ports.git.add_worktree(
                request.repo_root,
                path=candidate.path,
                branch=candidate.branch,
            )
        except Exception as exc:
            if is_retryable_git_collision(exc):
                refreshed = self._try_refresh_records(request.repo_root)
                if refreshed is not None:
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
                    )
            artifacts = self._observe_artifacts(request.repo_root, candidate, namespace)
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
        result = CreateResult(
            id=result.id,
            main_worktree_path=result.main_worktree_path,
            container_path=result.container_path,
            worktree_path=result.worktree_path,
            branch=result.branch,
            bootstrap=bootstrap,
            artifacts=result.artifacts,
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

    def _resolve_root(self, root: Path) -> Path:
        try:
            return validate_root(root, self.ports.filesystem, allow_missing=True)
        except RootResolutionError as exc:
            raise self._error(code=exc.code, message=str(exc), details=exc.details) from exc
        except Exception as exc:
            raise self._error(
                code="invalid_root",
                message=f"failed to inspect worktree root: {root}",
                details={"root": str(root), "diagnostic": _bounded(str(exc))},
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

    def _worktree_records(self, repo_root: Path) -> list[GitWorktreeRecord]:
        try:
            return list(self.ports.git.worktree_list(repo_root))
        except Exception as exc:
            raise self._error(
                code="git_worktree_list_failed",
                message="failed to list Git worktrees",
                details={"diagnostic": _bounded(str(exc))},
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

    def _result_after_add(
        self,
        request: CreateRequest,
        *,
        candidate: WorktreeCandidate,
        root: Path,
        namespace: Path,
        main_worktree_path: Path,
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

    def _try_refresh_records(self, repo_root: Path) -> list[GitWorktreeRecord] | None:
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
        records: list[GitWorktreeRecord],
        failed_candidate: WorktreeCandidate,
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
            collision = self._preflight_collision(request.repo_root, candidate, known_paths)
            if collision is not None:
                continue
            self._ensure_create_directories(root, namespace, request.repo_root, candidate)
            try:
                validate_namespace(root, repo_basename, self.ports.filesystem)
            except RootResolutionError as exc:
                raise self._error(code=exc.code, message=str(exc), details=exc.details) from exc
            try:
                self.ports.git.add_worktree(
                    request.repo_root,
                    path=candidate.path,
                    branch=candidate.branch,
                )
            except Exception as exc:
                if is_retryable_git_collision(exc):
                    refreshed = self._try_refresh_records(request.repo_root)
                    if refreshed is not None:
                        known_paths = {_record_canonical_path(record) for record in refreshed}
                        continue
                artifacts = self._observe_artifacts(request.repo_root, candidate, namespace)
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
            result = CreateResult(
                id=result.id,
                main_worktree_path=result.main_worktree_path,
                container_path=result.container_path,
                worktree_path=result.worktree_path,
                branch=result.branch,
                bootstrap=bootstrap,
                artifacts=result.artifacts,
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

        raise self._error(
            code="candidate_exhausted",
            message="worktree create exhausted candidate attempts",
            details={"attempts": MAX_CANDIDATE_ATTEMPTS},
        )

    def _error(
        self,
        *,
        code: str,
        message: str,
        details: Mapping[str, object],
        result: object | None = None,
        status: str = "error",
    ) -> ExpectedError:
        # The typed Literal contract is intentionally enforced at the shared
        # boundary; local adapter diagnostics cannot invent a public status.
        response_status = "partial" if status == "partial" else "error"
        return ExpectedError(
            code=code,  # type: ignore[arg-type]
            operation="create",
            message=message,
            details=details,
            result=result,
            status=response_status,  # type: ignore[arg-type]
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

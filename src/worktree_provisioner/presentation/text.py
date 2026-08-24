"""Human-readable text rendering for the worktree-provisioner CLI."""

from __future__ import annotations

from pathlib import Path

from worktree_provisioner.application.contracts import (
    CreateResult,
    ExpectedError,
    ListResult,
    RemoveResult,
    ResultWarning,
    ShowResult,
)


def render_success(result: CreateResult | ListResult | ShowResult | RemoveResult) -> str:
    if isinstance(result, CreateResult):
        command = " ".join(result.bootstrap.command) if result.bootstrap.command else "-"
        exit_code = "" if result.bootstrap.exit_code is None else f" exit_code={result.bootstrap.exit_code}"
        worktree_path = _absolute_path(result.worktree_path)
        return "\n".join(
            (
                f"worktree-provisioner: ok (create) id={result.id} branch={result.branch} path={worktree_path}",
                f"worktree-provisioner: bootstrap status={result.bootstrap.status} command={command}{exit_code}",
            )
        )
    if isinstance(result, ListResult):
        lines = [f"worktree-provisioner: ok (list) count={len(result.worktrees)}"]
        lines.extend(f"{item.id}\t{item.branch or '-'}\t{_absolute_path(item.path)}" for item in result.worktrees)
        return "\n".join(lines)
    if isinstance(result, ShowResult):
        worktree_path = _absolute_path(result.worktree.path)
        return (
            "worktree-provisioner: ok (show) "
            f"id={result.worktree.id} branch={result.worktree.branch or '-'} path={worktree_path}"
        )
    return (
        "worktree-provisioner: ok (remove) "
        f"target={result.target} path={_absolute_path(result.resolved_target.path)} "
        f"removed_record={result.removed_record} removed_directory={result.removed_directory}"
    )


def render_error(error: ExpectedError) -> tuple[str, str]:
    """Return ``(stdout, stderr)`` for a text-mode expected error."""

    stdout = ""
    if isinstance(error.result, CreateResult):
        create_result = error.result
        command = " ".join(create_result.bootstrap.command) if create_result.bootstrap.command else "-"
        exit_code = (
            "" if create_result.bootstrap.exit_code is None else f" exit_code={create_result.bootstrap.exit_code}"
        )
        worktree_path = _absolute_path(create_result.worktree_path)
        stdout = "\n".join(
            (
                "worktree-provisioner: partial (create) "
                f"id={create_result.id} branch={create_result.branch} path={worktree_path}",
                f"worktree-provisioner: bootstrap status={create_result.bootstrap.status} command={command}{exit_code}",
            )
        )
    elif isinstance(error.result, RemoveResult):
        remove_result = error.result
        stdout = (
            "worktree-provisioner: partial (remove) "
            f"target={remove_result.target} path={_absolute_path(remove_result.resolved_target.path)} "
            f"removed_record={remove_result.removed_record} removed_directory={remove_result.removed_directory}"
        )
    stderr_lines = [f"worktree-provisioner: error: {error.message}"]
    stderr_lines.extend(render_warnings(error.result))
    stderr = "\n".join(stderr_lines)
    return stdout, stderr


def render_warnings(result: object) -> tuple[str, ...]:
    if not isinstance(result, CreateResult):
        return ()
    return tuple(_warning_line(warning) for warning in result.warnings)


def _warning_line(warning: ResultWarning) -> str:
    message = warning.message.replace("\r", "\\r").replace("\n", "\\n")
    safe_message = message.encode("utf-8", errors="backslashreplace").decode("utf-8")
    return f"worktree-provisioner: warning: code={warning.code} message={safe_message}"


def _absolute_path(path: Path) -> str:
    candidate = path.expanduser()
    return str(candidate if candidate.is_absolute() else candidate.absolute())


__all__ = ["render_error", "render_success", "render_warnings"]

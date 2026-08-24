"""Human-readable text rendering for the worktree-provisioner CLI."""

from __future__ import annotations

from collections.abc import Sequence
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
                f"worktree-provisioner: ok (create) id={_safe_text(result.id)} "
                f"branch={_safe_text(result.branch)} path={worktree_path}",
                f"worktree-provisioner: bootstrap status={result.bootstrap.status} command={command}{exit_code}",
            )
        )
    if isinstance(result, ListResult):
        lines = [f"worktree-provisioner: ok (list) count={len(result.worktrees)}"]
        lines.extend(
            f"{_safe_text(item.id)}\t{_safe_text(item.branch or '-')}\t{_absolute_path(item.path)}"
            for item in result.worktrees
        )
        return "\n".join(lines)
    if isinstance(result, ShowResult):
        worktree_path = _absolute_path(result.worktree.path)
        return (
            "worktree-provisioner: ok (show) "
            f"id={_safe_text(result.worktree.id)} "
            f"branch={_safe_text(result.worktree.branch or '-')} path={worktree_path}"
        )
    return (
        "worktree-provisioner: ok (remove) "
        f"target={_safe_text(result.target)} path={_absolute_path(result.resolved_target.path)} "
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
                f"id={_safe_text(create_result.id)} branch={_safe_text(create_result.branch)} path={worktree_path}",
                f"worktree-provisioner: bootstrap status={create_result.bootstrap.status} command={command}{exit_code}",
            )
        )
    elif isinstance(error.result, RemoveResult):
        remove_result = error.result
        stdout = (
            "worktree-provisioner: partial (remove) "
            f"target={_safe_text(remove_result.target)} path={_absolute_path(remove_result.resolved_target.path)} "
            f"removed_record={remove_result.removed_record} removed_directory={remove_result.removed_directory}"
        )
    stderr_lines = [f"worktree-provisioner: error: {_safe_text(error.message)}"]
    stderr_lines.extend(render_warnings(error.result, warnings=error.warnings))
    stderr = "\n".join(stderr_lines)
    return stdout, stderr


def render_warnings(
    result: object | None = None,
    *,
    warnings: Sequence[ResultWarning] = (),
) -> tuple[str, ...]:
    selected = tuple(warnings)
    if not selected and isinstance(result, CreateResult):
        selected = result.warnings
    return tuple(_warning_line(warning) for warning in selected)


def _warning_line(warning: ResultWarning) -> str:
    safe_message = _safe_text(warning.message)
    line = f"worktree-provisioner: warning: code={warning.code} message={safe_message}"
    if warning.facts:
        facts = " ".join(f"{key}={_safe_fact(value)}" for key, value in sorted(warning.facts.items()))
        line += f" facts={facts}"
    return line


def _safe_fact(value: object) -> str:
    return _safe_text(value)


def _absolute_path(path: Path) -> str:
    candidate = path.expanduser()
    return _safe_text(candidate if candidate.is_absolute() else candidate.absolute())


def _safe_text(value: object) -> str:
    rendered = str(value).replace("\r", "\\r").replace("\n", "\\n")
    return rendered.encode("ascii", errors="backslashreplace").decode("ascii")


__all__ = ["render_error", "render_success", "render_warnings"]

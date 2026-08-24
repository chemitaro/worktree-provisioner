"""Command-line composition root for the four worktree operations."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Final, NoReturn, cast

from worktree_provisioner import __version__
from worktree_provisioner.application.contracts import (
    CreateRequest,
    CreateResult,
    ErrorCode,
    ExpectedError,
    ListRequest,
    ListResult,
    Operation,
    RemoveRequest,
    RemoveResult,
    ShowRequest,
    ShowResult,
)
from worktree_provisioner.application.ports import ApplicationPorts
from worktree_provisioner.application.root_and_naming import RootResolutionError, select_root
from worktree_provisioner.application.worktree_service import WorktreeService
from worktree_provisioner.infra.environment import EnvironmentAdapter
from worktree_provisioner.infra.filesystem import FilesystemCliGateway
from worktree_provisioner.infra.git_cli import GitCliGateway
from worktree_provisioner.infra.make_cli import MakeCliGateway
from worktree_provisioner.presentation import json_v1, text

_COMMANDS: Final[tuple[str, ...]] = ("create", "list", "show", "remove")
_RESULT = CreateResult | ListResult | ShowResult | RemoveResult


class _UsageParseError(ValueError):
    pass


class _ParserExit(SystemExit):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(status)


class _ArgumentParser(argparse.ArgumentParser):
    """Argparse boundary that lets ``main`` render JSON usage errors."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> NoReturn:
        raise _UsageParseError(message)

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        if message:
            print(message, file=sys.stderr, end="")
        raise _ParserExit(status)


def build_parser(
    raw_argv: Sequence[str] | None = None,
    *,
    json_mode: bool | None = None,
) -> argparse.ArgumentParser:
    # ``raw_argv`` is accepted for callers following the design document's
    # composition-root shape.  The parser itself only needs the flag at the
    # main boundary, where parse failures can be rendered as JSON.
    del raw_argv, json_mode
    parser = _ArgumentParser(prog="worktree-provisioner")
    parser.add_argument("--version", action="version", version=__version__)
    # Accepting --json before the command is harmless and makes the explicit
    # machine-mode promise unambiguous for agent callers.  The canonical
    # command-local spelling is also installed below.
    parser.add_argument("--json", action="store_true", dest="_global_json", help=argparse.SUPPRESS)
    subcommands = parser.add_subparsers(
        dest="command",
        required=True,
        parser_class=_ArgumentParser,
    )

    create = subcommands.add_parser("create", help="Create a central-root Git linked worktree")
    create.add_argument("label", nargs="?", help="Optional lowercase label: letters, digits, and hyphens")
    _add_common_arguments(create)
    create.add_argument("--no-bootstrap", action="store_true", help="Do not run the repository make init bootstrap")

    list_command = subcommands.add_parser("list", help="List Git linked worktrees")
    _add_common_arguments(list_command)

    show = subcommands.add_parser("show", help="Show one Git linked worktree")
    show.add_argument("target")
    _add_common_arguments(show)

    remove = subcommands.add_parser("remove", help="Remove one managed Git linked worktree")
    remove.add_argument("target")
    _add_common_arguments(remove)
    remove.add_argument("--force", action="store_true", help="Pass one explicit force to Git")
    return parser


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository or linked worktree path")
    parser.add_argument("--root", help="Managed worktree root; overrides WORKTREE_PROVISIONER_ROOT")
    parser.add_argument("--json", action="store_true", help="Emit the versioned agent JSON envelope")


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    explicit_json = "--json" in raw_argv
    parser = build_parser(json_mode=explicit_json)
    try:
        args = parser.parse_args(raw_argv)
    except _ParserExit as exc:
        return exc.status
    except _UsageParseError as exc:
        if explicit_json:
            print(json_v1.dumps(json_v1.usage_error_document(str(exc), operation=_infer_operation(raw_argv))))
        else:
            parser.print_usage(file=sys.stderr)
            print(f"worktree-provisioner: error: {exc}", file=sys.stderr)
        return 2

    json_mode = bool(getattr(args, "_global_json", False) or getattr(args, "json", False))
    operation = _operation_from_argument(getattr(args, "command", None))
    ports = ApplicationPorts(
        git=GitCliGateway(),
        bootstrap=MakeCliGateway(),
        filesystem=FilesystemCliGateway(),
        environment=EnvironmentAdapter(),
    )
    try:
        try:
            repo_root = ports.git.resolve_checkout_root(args.repo)
        except Exception as exc:
            raise ExpectedError(
                code=_repository_error_code(exc),
                operation=operation,
                message="failed to resolve the invocation repository",
                details={"diagnostic": _bounded(str(exc))},
                result=None,
                status="error",
            ) from exc
        try:
            root = select_root(args.root, ports.environment)
        except RootResolutionError as exc:
            raise ExpectedError(
                code=cast(ErrorCode, exc.code),
                operation=operation,
                message=str(exc),
                details=exc.details,
                result=None,
                status="error",
            ) from exc

        service = WorktreeService(ports)
        result = _dispatch(service, args, repo_root, root, operation)
    except ExpectedError as exc:
        return _emit_error(exc, json_mode=json_mode)
    except Exception as exc:
        internal = ExpectedError(
            code="internal_error",
            operation=operation,
            message="unexpected internal error",
            details={"exception_type": type(exc).__name__},
            result=None,
            status="error",
        )
        # Preserve the original cause for in-process diagnostics without
        # serializing its message or traceback to either user-facing stream.
        internal.__cause__ = exc
        return _emit_error(internal, json_mode=json_mode)

    _emit_success(result, json_mode=json_mode)
    return 0


def _dispatch(
    service: WorktreeService,
    args: argparse.Namespace,
    repo_root: Path,
    root: Path,
    operation: Operation,
) -> _RESULT:
    if operation == "create":
        return service.create(
            CreateRequest(
                repo_root=repo_root,
                root=root,
                label=args.label,
                bootstrap_enabled=not args.no_bootstrap,
            )
        )
    if operation == "list":
        return service.list(ListRequest(repo_root=repo_root, root=root))
    if operation == "show":
        return service.show(ShowRequest(repo_root=repo_root, root=root, target=args.target))
    return service.remove(RemoveRequest(repo_root=repo_root, root=root, target=args.target, force=args.force))


def _emit_success(result: _RESULT, *, json_mode: bool) -> None:
    if json_mode:
        print(json_v1.dumps(json_v1.success_document(result)))
    else:
        print(text.render_success(result))


def _emit_error(error: ExpectedError, *, json_mode: bool) -> int:
    if json_mode:
        print(json_v1.dumps(json_v1.error_document(error)))
    else:
        stdout, stderr = text.render_error(error)
        if stdout:
            print(stdout)
        print(stderr, file=sys.stderr)
    return 1


def _operation_from_argument(value: object) -> Operation:
    if value in _COMMANDS:
        return cast(Operation, value)
    return "create"


def _infer_operation(argv: Sequence[str]) -> str | None:
    for item in argv:
        if item in _COMMANDS:
            return item
    return None


def _repository_error_code(error: BaseException) -> ErrorCode:
    diagnostic = " ".join(
        part
        for part in (
            str(error),
            str(getattr(error, "diagnostic", "")),
        )
        if part
    ).lower()
    if "bare repository" in diagnostic or "must be run in a work tree" in diagnostic:
        return "bare_repository_unsupported"
    if "not found" in diagnostic or "executable" in diagnostic or "start git" in diagnostic:
        return "git_unavailable"
    return "repository_unavailable"


def _bounded(value: str, limit: int = 4096) -> str:
    if len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return f"{value[: limit - 1]}…"


__all__ = ["build_parser", "main"]

"""Command-line composition root for the worktree lifecycle commands."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path

from worktree_provisioner import __version__
from worktree_provisioner.application.contracts import (
    ArtifactState,
    BootstrapResult,
    CreateRequest,
    CreateResult,
    ErrorCode,
    ExpectedError,
    ListRequest,
    ListResult,
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="worktree-provisioner")
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command", required=True)

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
    args = build_parser().parse_args(argv)

    ports = ApplicationPorts(
        git=GitCliGateway(),
        bootstrap=MakeCliGateway(),
        filesystem=FilesystemCliGateway(),
        environment=EnvironmentAdapter(),
    )
    json_mode = bool(args.json)
    try:
        try:
            repo_root = ports.git.resolve_checkout_root(args.repo)
        except Exception as exc:
            raise ExpectedError(
                code=_repository_error_code(exc),
                operation=args.command,
                message="failed to resolve the invocation repository",
                details={"diagnostic": _bounded(str(exc))},
                result=None,
                status="error",
            ) from exc
        try:
            root = select_root(args.root, ports.environment)
        except RootResolutionError as exc:
            raise ExpectedError(
                code=exc.code,  # type: ignore[arg-type]
                operation=args.command,
                message=str(exc),
                details=exc.details,
                result=None,
                status="error",
            ) from exc
        service = WorktreeService(ports)
        if args.command == "create":
            result: CreateResult | ListResult | ShowResult | RemoveResult = service.create(
                CreateRequest(
                    repo_root=repo_root,
                    root=root,
                    label=args.label,
                    bootstrap_enabled=not args.no_bootstrap,
                )
            )
        elif args.command == "list":
            result = service.list(ListRequest(repo_root=repo_root, root=root))
        elif args.command == "show":
            result = service.show(ShowRequest(repo_root=repo_root, root=root, target=args.target))
        else:
            result = service.remove(
                RemoveRequest(
                    repo_root=repo_root,
                    root=root,
                    target=args.target,
                    force=args.force,
                )
            )
    except ExpectedError as exc:
        return _emit_error(exc, json_mode=json_mode)
    except Exception as exc:
        internal = ExpectedError(
            code="internal_error",
            operation=args.command,
            message="unexpected internal error",
            details={"diagnostic": _bounded(str(exc))},
            result=None,
            status="error",
        )
        return _emit_error(internal, json_mode=json_mode)

    _emit_success(result, json_mode=json_mode)
    return 0


def _emit_success(result: CreateResult | ListResult | ShowResult | RemoveResult, *, json_mode: bool) -> None:
    if json_mode:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "ok",
                    "operation": _result_operation(result),
                    "result": _result_payload(result),
                    "error": None,
                    "warnings": [],
                },
                ensure_ascii=False,
            )
        )
        return
    if isinstance(result, CreateResult):
        print(f"worktree-provisioner: ok (create) id={result.id} branch={result.branch} path={result.worktree_path}")
        command = " ".join(result.bootstrap.command) if result.bootstrap.command else "-"
        exit_code = "" if result.bootstrap.exit_code is None else f" exit_code={result.bootstrap.exit_code}"
        print(f"worktree-provisioner: bootstrap status={result.bootstrap.status} command={command}{exit_code}")
    elif isinstance(result, ListResult):
        print(f"worktree-provisioner: ok (list) count={len(result.worktrees)}")
        for worktree in result.worktrees:
            branch = worktree.branch or "-"
            print(f"{worktree.id}\t{branch}\t{worktree.path}")
    elif isinstance(result, ShowResult):
        print(
            "worktree-provisioner: ok (show) "
            f"id={result.worktree.id} branch={result.worktree.branch or '-'} path={result.worktree.path}"
        )
    else:
        print(
            "worktree-provisioner: ok (remove) "
            f"target={result.target} path={result.resolved_target.path} "
            f"removed_record={result.removed_record} removed_directory={result.removed_directory}"
        )


def _emit_error(error: ExpectedError, *, json_mode: bool) -> int:
    if json_mode:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": error.status,
                    "operation": error.operation,
                    "result": _result_payload(error.result),
                    "error": {
                        "code": error.code,
                        "message": error.message,
                        "details": _json_safe(error.details),
                    },
                    "warnings": [],
                },
                ensure_ascii=False,
            )
        )
    else:
        if isinstance(error.result, CreateResult):
            result = error.result
            print(
                "worktree-provisioner: partial (create) "
                f"id={result.id} branch={result.branch} path={result.worktree_path}"
            )
            command = " ".join(result.bootstrap.command) if result.bootstrap.command else "-"
            exit_code = "" if result.bootstrap.exit_code is None else f" exit_code={result.bootstrap.exit_code}"
            print(f"worktree-provisioner: bootstrap status={result.bootstrap.status} command={command}{exit_code}")
        elif isinstance(error.result, RemoveResult):
            remove_result = error.result
            print(
                "worktree-provisioner: partial (remove) "
                f"target={remove_result.target} path={remove_result.resolved_target.path} "
                f"removed_record={remove_result.removed_record} "
                f"removed_directory={remove_result.removed_directory}"
            )
        print(f"worktree-provisioner: error: {error.message}", file=sys.stderr)
    return 1


def _create_payload(result: CreateResult) -> dict[str, object]:
    return {
        "id": result.id,
        "main_worktree_path": str(result.main_worktree_path),
        "container_path": str(result.container_path),
        "worktree_path": str(result.worktree_path),
        "branch": result.branch,
        "bootstrap": _bootstrap_payload(result.bootstrap),
        "artifacts": _artifact_payload(result.artifacts),
    }


def _list_payload(result: ListResult) -> dict[str, object]:
    return {"worktrees": [_json_safe(worktree) for worktree in result.worktrees]}


def _show_payload(result: ShowResult) -> dict[str, object]:
    return {"target": result.target, "worktree": _json_safe(result.worktree)}


def _result_payload(result: object | None) -> object | None:
    if isinstance(result, CreateResult):
        return _create_payload(result)
    if isinstance(result, ListResult):
        return _list_payload(result)
    if isinstance(result, ShowResult):
        return _show_payload(result)
    if isinstance(result, RemoveResult):
        return _remove_payload(result)
    return _json_safe(result)


def _result_operation(result: CreateResult | ListResult | ShowResult | RemoveResult) -> str:
    if isinstance(result, CreateResult):
        return "create"
    if isinstance(result, ListResult):
        return "list"
    if isinstance(result, ShowResult):
        return "show"
    return "remove"


def _remove_payload(result: RemoveResult) -> dict[str, object]:
    return {
        "target": result.target,
        "resolved_target": _json_safe(result.resolved_target),
        "force_requested": result.force_requested,
        "removed_record": result.removed_record,
        "removed_directory": result.removed_directory,
        "branch_deleted": result.branch_deleted,
    }


def _bootstrap_payload(result: BootstrapResult) -> dict[str, object]:
    return {
        "requested": result.requested,
        "status": result.status,
        "command": list(result.command) if result.command is not None else None,
        "exit_code": result.exit_code,
        "detail": result.detail,
    }


def _artifact_payload(result: ArtifactState) -> dict[str, bool | None]:
    return {
        "container_exists": result.container_exists,
        "worktree_path_exists": result.worktree_path_exists,
        "branch_exists": result.branch_exists,
        "worktree_record_exists": result.worktree_record_exists,
    }


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if is_dataclass(value):
        return _json_safe(asdict(value))  # type: ignore[arg-type]
    return str(value)


def _bounded(value: str, limit: int = 4096) -> str:
    if len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return f"{value[: limit - 1]}…"


def _repository_error_code(error: BaseException) -> ErrorCode:
    text = str(error).lower()
    if "not found" in text or "executable" in text or "start git" in text:
        return "git_unavailable"
    return "repository_unavailable"


__all__ = ["build_parser", "main"]

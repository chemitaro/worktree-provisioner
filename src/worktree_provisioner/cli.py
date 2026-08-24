"""Command-line composition root for the create vertical slice.

P4 wires ``create`` end-to-end.  The other command names are registered in
the parser so the package advertises the planned command family; inventory,
target resolution, and removal are intentionally supplied by later phases.
"""

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
    if args.command != "create":
        return _emit_error(
            ExpectedError(
                code="usage_error",
                operation=args.command,
                message=f"{args.command} is not implemented in this phase",
                details={"command": args.command},
                result=None,
                status="error",
            ),
            json_mode=bool(getattr(args, "json", False)),
        )

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
                operation="create",
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
                operation="create",
                message=str(exc),
                details=exc.details,
                result=None,
                status="error",
            ) from exc
        result = WorktreeService(ports).create(
            CreateRequest(
                repo_root=repo_root,
                root=root,
                label=args.label,
                bootstrap_enabled=not args.no_bootstrap,
            )
        )
    except ExpectedError as exc:
        return _emit_error(exc, json_mode=json_mode)
    except Exception as exc:
        internal = ExpectedError(
            code="internal_error",
            operation="create",
            message="unexpected internal error",
            details={"diagnostic": _bounded(str(exc))},
            result=None,
            status="error",
        )
        return _emit_error(internal, json_mode=json_mode)

    _emit_success(result, json_mode=json_mode)
    return 0


def _emit_success(result: CreateResult, *, json_mode: bool) -> None:
    if json_mode:
        print(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "ok",
                    "operation": "create",
                    "result": _create_payload(result),
                    "error": None,
                    "warnings": [],
                },
                ensure_ascii=False,
            )
        )
        return
    print(
        "worktree-provisioner: ok (create) "
        f"id={result.id} branch={result.branch} path={result.worktree_path}"
    )
    command = " ".join(result.bootstrap.command) if result.bootstrap.command else "-"
    exit_code = "" if result.bootstrap.exit_code is None else f" exit_code={result.bootstrap.exit_code}"
    print(f"worktree-provisioner: bootstrap status={result.bootstrap.status} command={command}{exit_code}")


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


def _result_payload(result: object | None) -> object | None:
    if isinstance(result, CreateResult):
        return _create_payload(result)
    return _json_safe(result)


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

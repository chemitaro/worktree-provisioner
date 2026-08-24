from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from worktree_provisioner import __version__
from worktree_provisioner.core import CreateResult, create_worktree


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="worktree-provisioner")
    parser.add_argument("--version", action="version", version=__version__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    create = subcommands.add_parser("create", help="Create a central-root Git linked worktree")
    create.add_argument("label", nargs="?", help="Optional lowercase label: letters, digits, and hyphens")
    create.add_argument("--repo", type=Path, default=Path.cwd(), help="Repository or linked worktree path")
    create.add_argument("--root", help="Central worktree root; overrides environment variables")
    create.add_argument("--json", action="store_true", help="Emit agent-oriented JSON output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            result = create_worktree(repo=args.repo, root=args.root, label=args.label)
            _render_create(result, as_json=args.json)
            return 0
    except RuntimeError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "error": {"message": str(exc)}}, ensure_ascii=False))
        else:
            print(f"worktree-provisioner: error: {exc}", file=sys.stderr)
        return 1
    raise RuntimeError(f"unsupported command: {args.command}")


def _render_create(result: CreateResult, *, as_json: bool) -> None:
    if as_json:
        payload = asdict(result)
        payload["main_worktree_path"] = str(result.main_worktree_path)
        payload["container_path"] = str(result.container_path)
        payload["worktree_path"] = str(result.worktree_path)
        print(json.dumps({"status": "ok", "operation": "create", "result": payload}, ensure_ascii=False))
    else:
        print(
            "worktree-provisioner: ok (create) "
            f"id={result.id} branch={result.branch_name} path={result.worktree_path}"
        )
        command = result.bootstrap.command or "-"
        print(f"worktree-provisioner: bootstrap status={result.bootstrap.status} command={command}")
    for warning in result.bootstrap.warnings:
        print(f"worktree-provisioner: warning: {warning}", file=sys.stderr)


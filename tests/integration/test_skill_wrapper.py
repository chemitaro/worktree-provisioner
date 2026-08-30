from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[2]
WRAPPER = REPOSITORY_ROOT / "skills" / "worktree-provisioner" / "scripts" / "worktree-provisioner"
SKILL = REPOSITORY_ROOT / "skills" / "worktree-provisioner" / "SKILL.md"


@pytest.mark.parametrize("exit_code", [0, 1, 2])
def test_wrapper_propagates_argv_streams_and_exit(fake_cli_on_path: Path, exit_code: int) -> None:
    if not WRAPPER.exists():
        raise AssertionError(f"thin wrapper is not implemented yet: {WRAPPER}")

    environment = dict(os.environ)
    environment["PATH"] = f"{fake_cli_on_path}{os.pathsep}{environment.get('PATH', '')}"
    environment["FAKE_WTP_EXIT"] = str(exit_code)
    environment.pop("WORKTREE_PROVISIONER_ROOT", None)
    environment.pop("SPEC_DOCK_WORKTREE_ROOT", None)
    arguments = ("remove", "target with spaces;$(touch nope)", "--force", "--json")
    result = subprocess.run(
        [str(WRAPPER), *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == exit_code
    assert json.loads(result.stdout) == list(arguments)
    assert result.stderr.strip() == "fake stderr"


def test_wrapper_reports_missing_cli_without_mutating_arguments(tmp_path: Path) -> None:
    empty_path = tmp_path / "empty-bin"
    empty_path.mkdir()
    environment = dict(os.environ)
    environment["PATH"] = str(empty_path)
    arguments = ("list", "--repo", "/repo with spaces", "--json")

    result = subprocess.run(
        [str(WRAPPER), *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 127
    assert result.stdout == ""
    assert result.stderr == "worktree-provisioner is not installed or not available on PATH\n"


def test_wrapper_is_exactly_thin_and_executable() -> None:
    source = WRAPPER.read_text(encoding="utf-8")
    assert WRAPPER.stat().st_mode & stat.S_IXUSR
    assert source == (
        "#!/bin/sh\n"
        "set -eu\n"
        "if ! command -v worktree-provisioner >/dev/null 2>&1; then\n"
        '  echo "worktree-provisioner is not installed or not available on PATH" >&2\n'
        "  exit 127\n"
        "fi\n"
        'exec worktree-provisioner "$@"\n'
    )
    for forbidden in ("git", "make", "WORKTREE_PROVISIONER_ROOT", "json", "target"):
        assert forbidden.lower() not in source.lower()


def test_skill_documents_explicit_force_authorization() -> None:
    if not SKILL.exists():
        raise AssertionError(f"skill source is not implemented yet: {SKILL}")

    content = SKILL.read_text(encoding="utf-8")
    lowered = content.lower()
    assert "--force" in content
    assert "force" in lowered
    assert "explicit" in lowered or "明示" in content
    assert "show" in lowered
    assert "remove" in lowered


def test_skill_frontmatter_is_discoverable_and_scoped() -> None:
    content = SKILL.read_text(encoding="utf-8")
    assert content.startswith("---\n")
    frontmatter, separator, _body = content[4:].partition("\n---\n")
    assert separator
    assert "name: worktree-provisioner" in frontmatter
    assert "description:" in frontmatter
    assert "Git linked worktree" in frontmatter
    assert "Codex" in frontmatter
    assert "task/workflow" in frontmatter
    assert "disable-model-invocation" not in content


def test_skill_uses_json_and_cli_authority_for_each_operation() -> None:
    content = SKILL.read_text(encoding="utf-8")
    for operation in ("create", "list", "show", "remove"):
        assert operation in content
    assert content.count("--json") >= 4
    assert "schema_version" in content
    assert "status" in content
    assert "error.code" in content
    assert "text output の解析で判断しない" in content
    assert "CLI に委ねます" in content
    assert "root precedence" in content
    assert "target resolution" in content


def test_skill_create_requires_fact_gate_and_reports_partial_artifacts() -> None:
    content = SKILL.read_text(encoding="utf-8")
    assert "fact gate" in content
    assert "status=ok" in content
    assert "create を一度だけ" in content
    assert "label 省略" in content
    assert "result.id" in content
    assert "result.branch" in content
    assert "result.worktree_path" in content
    assert "result.bootstrap.status" in content
    assert "retained worktree/branch" in content
    assert "Codex task lifecycle は変更していない" in content


def test_skill_remove_requires_show_gate_and_explicit_force() -> None:
    content = SKILL.read_text(encoding="utf-8")
    assert "明示的な target removal" in content
    assert "show <target>" in content
    assert "managed=true" in content
    assert "removable=true" in content
    assert "remove_blockers=[]" in content
    assert "force の利用者明示がない限り `--force` を付けず" in content
    assert "ちょうど一度" in content
    assert "git worktree unlock" in content
    assert "unlock や再試行を実行しない" in content


def test_skill_has_no_task_lifecycle_or_implicit_force_authorization() -> None:
    content = SKILL.read_text(encoding="utf-8")
    assert "Codex task の作成・移動" in content
    assert "force intent" in content
    assert "force の利用者明示" in content
    assert "force intent が明示されていない呼び出しに `--force` がなく" in content

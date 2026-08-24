from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
WRAPPER = REPOSITORY_ROOT / "skills" / "worktree-provisioner" / "scripts" / "worktree-provisioner"
SKILL = REPOSITORY_ROOT / "skills" / "worktree-provisioner" / "SKILL.md"


def test_wrapper_propagates_argv_streams_and_exit(fake_cli_on_path: Path) -> None:
    if not WRAPPER.exists():
        raise AssertionError(f"thin wrapper is not implemented yet: {WRAPPER}")

    environment = dict(os.environ)
    environment["PATH"] = f"{fake_cli_on_path}{os.pathsep}{environment.get('PATH', '')}"
    environment["FAKE_WTP_EXIT"] = "7"
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

    assert result.returncode == 7
    assert json.loads(result.stdout) == list(arguments)
    assert result.stderr.strip() == "fake stderr"


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

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]
PYPROJECT = REPOSITORY_ROOT / "pyproject.toml"
CI_WORKFLOW = REPOSITORY_ROOT / ".github/workflows/ci.yml"


def _workflow_matrix(workflow: str) -> str:
    matches = list(re.finditer(r"(?m)^ {6}matrix:\n", workflow))
    assert len(matches) == 1, "CI must define exactly one test matrix"
    match = re.search(r"(?ms)^ {6}matrix:\n(?P<body>.*?)(?=^ {4}steps:\n)", workflow)
    assert match is not None, "CI test matrix must be followed by its steps"
    return match.group("body")


def _matrix_list(matrix: str, key: str) -> tuple[str, ...]:
    match = re.search(rf"(?m)^ {{8}}{re.escape(key)}:\n(?P<items>(?:^ {{10}}- .*\n)+)", matrix)
    assert match is not None, key
    values = []
    for line in match.group("items").splitlines():
        value = line.removeprefix(" " * 10 + "- ").strip()
        values.append(value.strip("\"'"))
    return tuple(values)


def test_distribution_contract_is_pinned_in_project_and_ci_metadata() -> None:
    pyproject = PYPROJECT.read_text(encoding="utf-8")
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")

    for fragment in (
        "[project]",
        'name = "worktree-provisioner"',
        'version = "0.1.0"',
        'requires-python = ">=3.10"',
        'license = { text = "MIT" }',
        '"Operating System :: MacOS"',
        '"Operating System :: POSIX :: Linux"',
        'packages = ["src/worktree_provisioner"]',
        'worktree-provisioner = "worktree_provisioner.cli:main"',
    ):
        assert fragment in pyproject, fragment
    assert "Windows" not in pyproject

    matrix = _workflow_matrix(workflow)
    assert tuple(re.findall(r"(?m)^ {8}([a-z][a-z0-9-]*):$", matrix)) == ("os", "python-version")
    assert _matrix_list(matrix, "os") == ("ubuntu-latest", "macos-latest")
    assert _matrix_list(matrix, "python-version") == ("3.10", "3.11", "3.12", "3.13")

    for fragment in (
        "ubuntu-latest",
        "macos-latest",
        '"3.10"',
        '"3.11"',
        '"3.12"',
        '"3.13"',
        "uv sync --locked --all-groups",
        "uv run ruff format --check .",
        "uv run ruff check .",
        "uv run mypy src tests",
        "uv run pytest -q",
        "uv build",
        "python -m venv",
        "pip install --no-index --no-deps",
        "--help",
        "--version",
    ):
        assert fragment in workflow, fragment

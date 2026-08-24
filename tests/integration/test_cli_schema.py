from __future__ import annotations

from pathlib import Path
from typing import Any

from conftest import TempGitRepository  # type: ignore[import-not-found]


def test_json_usage_error_is_one_stdout_document_with_exit_two(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    result = cli_runner("show", "--json", repo=temp_git_repo.path, root=central_root)
    payload: dict[str, Any] = json_loads(result.stdout)

    assert result.returncode == 2
    assert result.stderr == ""
    assert set(payload) == {"schema_version", "status", "operation", "result", "error", "warnings"}
    assert payload["schema_version"] == 1
    assert payload["status"] == "error"
    assert payload["operation"] == "show"
    assert payload["result"] is None
    assert payload["error"]["code"] == "usage_error"
    assert isinstance(payload["error"]["details"], dict)
    assert payload["warnings"] == []


def test_unknown_json_command_has_null_operation_and_no_stderr(cli_runner, json_loads) -> None:
    result = cli_runner("unknown-command", "--json")
    payload: dict[str, Any] = json_loads(result.stdout)

    assert result.returncode == 2
    assert result.stderr == ""
    assert payload["operation"] is None
    assert payload["error"]["code"] == "usage_error"
    assert payload["result"] is None


def test_missing_json_command_is_usage_error_with_exit_two(cli_runner, json_loads) -> None:
    result = cli_runner("--json")
    payload: dict[str, Any] = json_loads(result.stdout)

    assert result.returncode == 2
    assert result.stderr == ""
    assert payload["operation"] is None
    assert payload["error"]["code"] == "usage_error"


def test_unknown_json_option_is_usage_error_with_exit_two(
    temp_git_repo: TempGitRepository, central_root: Path, cli_runner, json_loads
) -> None:
    result = cli_runner(
        "show",
        "feature",
        "--json",
        "--unknown-option",
        repo=temp_git_repo.path,
        root=central_root,
    )
    payload: dict[str, Any] = json_loads(result.stdout)

    assert result.returncode == 2
    assert result.stderr == ""
    assert payload["operation"] == "show"
    assert payload["error"]["code"] == "usage_error"


def test_command_help_and_version_are_text_successes(cli_runner) -> None:
    help_result = cli_runner("remove", "--help")
    version_result = cli_runner("--version")

    assert help_result.returncode == 0
    assert help_result.stderr == ""
    assert "--force" in help_result.stdout
    assert version_result.returncode == 0
    assert version_result.stderr == ""
    assert version_result.stdout.strip()

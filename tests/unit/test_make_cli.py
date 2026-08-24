from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from worktree_provisioner.application.contracts import BootstrapResult
from worktree_provisioner.infra.make_cli import MakeAdapterError, MakeCliGateway


def _completed(returncode: int, *, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["make"], returncode, stdout=stdout, stderr=stderr)


def _runner_for(
    responses: list[subprocess.CompletedProcess[str]],
) -> tuple[list[tuple[list[str], dict[str, object]]], object]:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(argv, **kwargs):
        calls.append((argv, kwargs))
        return responses.pop(0)

    return calls, runner


def test_no_makefile_is_skipped_without_invoking_make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("worktree_provisioner.infra.make_cli.shutil.which", lambda _: pytest.fail("make lookup"))

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result == BootstrapResult(True, "skipped", None, None, None)


def test_no_init_target_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "Makefile").write_text("all:\n\t@echo all\n", encoding="utf-8")
    calls, runner = _runner_for(
        [_completed(2, stderr="make: *** No rule to make target 'init'. Stop.\n")]
    )
    monkeypatch.setattr("worktree_provisioner.infra.make_cli.shutil.which", lambda _: "/usr/bin/make")

    result = MakeCliGateway(runner=runner).run_make_init_if_available(tmp_path)

    assert result.status == "skipped"
    assert result.command is None
    assert len(calls) == 1
    assert calls[0][0] == ["make", "-n", "init"]


def test_make_unavailable_is_detection_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "GNUmakefile").write_text("init:\n", encoding="utf-8")
    monkeypatch.setattr("worktree_provisioner.infra.make_cli.shutil.which", lambda _: None)

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "detection_failed"
    assert result.command == ("make", "-n", "init")
    assert result.exit_code is None
    assert "not found" in (result.detail or "")


def test_parse_or_include_error_is_detection_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "makefile").write_text("include missing.mk\ninit:\n", encoding="utf-8")
    _calls, runner = _runner_for([_completed(2, stderr="Makefile:1: missing.mk: No such file or directory")])
    monkeypatch.setattr("worktree_provisioner.infra.make_cli.shutil.which", lambda _: "/usr/bin/make")

    result = MakeCliGateway(runner=runner).run_make_init_if_available(tmp_path)

    assert result.status == "detection_failed"
    assert result.command == ("make", "-n", "init")
    assert result.exit_code == 2
    assert "missing.mk" in (result.detail or "")


def test_success_runs_detection_then_init_in_created_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "Makefile").write_text("init:\n\t@echo initialized\n", encoding="utf-8")
    calls, runner = _runner_for([_completed(0, stdout="echo initialized\n"), _completed(0)])
    monkeypatch.setattr("worktree_provisioner.infra.make_cli.shutil.which", lambda _: "/usr/bin/make")

    result = MakeCliGateway(runner=runner).run_make_init_if_available(tmp_path)

    assert result == BootstrapResult(True, "succeeded", ("make", "init"), 0, None)
    assert [call[0] for call in calls] == [["make", "-n", "init"], ["make", "init"]]
    assert all(call[1]["cwd"] == tmp_path for call in calls)
    assert all(call[1]["shell"] is False for call in calls)
    assert all(call[1]["capture_output"] is True for call in calls)
    assert all(call[1]["text"] is True for call in calls)
    assert all(call[1]["check"] is False for call in calls)


def test_make_init_failure_is_failed_and_diagnostic_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "Makefile").write_text("init:\n", encoding="utf-8")
    _calls, runner = _runner_for(
        [_completed(0), _completed(7, stdout="out" * 5000, stderr="err" * 5000)]
    )
    monkeypatch.setattr("worktree_provisioner.infra.make_cli.shutil.which", lambda _: "/usr/bin/make")

    result = MakeCliGateway(runner=runner).run_make_init_if_available(tmp_path)

    assert result.status == "failed"
    assert result.command == ("make", "init")
    assert result.exit_code == 7
    assert result.detail is not None
    assert len(result.detail) <= 4096
    assert "stderr:" in result.detail
    assert "stdout:" in result.detail


def test_make_runner_start_failure_is_reported_as_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "Makefile").write_text("init:\n", encoding="utf-8")
    responses = [_completed(0)]

    def runner(argv, **kwargs):
        if argv == ["make", "init"]:
            raise OSError("spawn failed")
        return responses.pop(0)

    monkeypatch.setattr("worktree_provisioner.infra.make_cli.shutil.which", lambda _: "/usr/bin/make")

    result = MakeCliGateway(runner=runner).run_make_init_if_available(tmp_path)

    assert result.status == "failed"
    assert result.exit_code == 1
    assert "spawn failed" in (result.detail or "")


def test_invalid_worktree_path_is_typed_error(tmp_path: Path) -> None:
    with pytest.raises(MakeAdapterError) as caught:
        MakeCliGateway().run_make_init_if_available(tmp_path / "missing")

    assert caught.value.operation == "validate_worktree_path"

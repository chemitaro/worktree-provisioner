from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from worktree_provisioner.application.contracts import BootstrapResult
from worktree_provisioner.infra.make_cli import MakeAdapterError, MakeCliGateway, MakeRunner


def _completed(returncode: int, *, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["make"], returncode, stdout=stdout, stderr=stderr)


def _runner_for(
    responses: list[subprocess.CompletedProcess[str]],
) -> tuple[list[tuple[list[str], dict[str, object]]], MakeRunner]:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(
        args: Sequence[str],
        *,
        cwd: Path,
        capture_output: bool,
        text: bool,
        check: bool,
        shell: bool,
    ) -> subprocess.CompletedProcess[str]:
        kwargs: dict[str, Any] = {
            "cwd": cwd,
            "capture_output": capture_output,
            "text": text,
            "check": check,
            "shell": shell,
        }
        calls.append((list(args), kwargs))
        return responses.pop(0)

    return calls, runner


def test_no_makefile_is_skipped_without_invoking_make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("worktree_provisioner.infra.make_cli.shutil.which", lambda _: pytest.fail("make lookup"))

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result == BootstrapResult(True, "skipped", None, None, None)


def test_no_init_target_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "Makefile").write_text("all:\n\t@echo all\n", encoding="utf-8")
    calls, runner = _runner_for([_completed(0, stdout="all:\n")])
    monkeypatch.setattr("worktree_provisioner.infra.make_cli.shutil.which", lambda _: "/usr/bin/make")

    result = MakeCliGateway(runner=runner).run_make_init_if_available(tmp_path)

    assert result.status == "skipped"
    assert result.command is None
    assert len(calls) == 1
    assert calls[0][0] == ["make", "-n", "init"]


def test_missing_target_diagnostic_spoof_is_detection_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "Makefile").write_text("$(error No rule to make target 'init')\n", encoding="utf-8")
    calls, runner = _runner_for([_completed(2, stderr="No rule to make target 'init'\n")])
    monkeypatch.setattr("worktree_provisioner.infra.make_cli.shutil.which", lambda _: "/usr/bin/make")

    result = MakeCliGateway(runner=runner).run_make_init_if_available(tmp_path)

    assert result.status == "detection_failed"
    assert result.command == ("make", "-n", "init")
    assert len(calls) == 1


def test_localized_missing_target_diagnostic_is_detection_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "Makefile").write_text("all:\n", encoding="utf-8")
    _calls, runner = _runner_for([_completed(2, stderr="No se encontró ninguna regla para init\n")])
    monkeypatch.setattr("worktree_provisioner.infra.make_cli.shutil.which", lambda _: "/usr/bin/make")

    result = MakeCliGateway(runner=runner).run_make_init_if_available(tmp_path)

    assert result.status == "detection_failed"


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
    calls, runner = _runner_for([_completed(0, stdout="init:\n"), _completed(0)])
    monkeypatch.setattr("worktree_provisioner.infra.make_cli.shutil.which", lambda _: "/usr/bin/make")

    result = MakeCliGateway(runner=runner).run_make_init_if_available(tmp_path)

    assert result == BootstrapResult(True, "succeeded", ("make", "init"), 0, None)
    assert [call[0] for call in calls] == [["make", "-n", "init"], ["make", "init"]]
    assert all(call[1]["cwd"] == tmp_path for call in calls)
    assert all(call[1]["shell"] is False for call in calls)
    assert all(call[1]["capture_output"] is True for call in calls)
    assert all(call[1]["text"] is True for call in calls)
    assert all(call[1]["check"] is False for call in calls)


def test_make_init_failure_is_failed_and_diagnostic_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "Makefile").write_text("init:\n", encoding="utf-8")
    _calls, runner = _runner_for(
        [_completed(0, stdout="init:\n"), _completed(7, stdout="out" * 5000, stderr="err" * 5000)]
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


def test_make_diagnostic_redacts_representative_secrets_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "Makefile").write_text("init:\n", encoding="utf-8")
    secrets = {
        "token-value-123",
        "password-value-456",
        "secret-value-789",
        "api-key-value-abc",
        "authorization-assignment-value-jkl",
        "authorization-value-def",
        "bearer-value-ghi",
    }
    diagnostic = (
        "TOKEN=token-value-123 PASSWORD: password-value-456 SECRET=secret-value-789 "
        "API_KEY=api-key-value-abc Authorization=authorization-assignment-value-jkl "
        "Authorization: Bearer authorization-value-def "
        "Bearer bearer-value-ghi\n" + "x" * 20_000
    )
    _calls, runner = _runner_for([_completed(0, stdout="init:\n"), _completed(7, stdout=diagnostic, stderr=diagnostic)])
    monkeypatch.setattr("worktree_provisioner.infra.make_cli.shutil.which", lambda _: "/usr/bin/make")

    result = MakeCliGateway(runner=runner).run_make_init_if_available(tmp_path)

    assert result.status == "failed"
    assert result.detail is not None
    assert len(result.detail) <= 4096
    for secret in secrets:
        assert secret not in result.detail
    assert "[REDACTED]" in result.detail


def test_real_make_capture_is_bounded_and_redacted(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(
        "init:\n\t@printf 'TOKEN=real-secret-value\\n' >&2\n\t@yes x | head -c 200000 >&2\n\t@exit 7\n",
        encoding="utf-8",
    )

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "failed"
    assert result.detail is not None
    assert len(result.detail) <= 4096
    assert "real-secret-value" not in result.detail
    assert "[REDACTED]" in result.detail


def test_real_make_structural_probe_finds_init_after_long_prelude(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    prelude = "".join(f"PRELUDE_{index} := value-{index}\n" for index in range(1500))
    (tmp_path / "Makefile").write_text(
        f"{prelude}init:\n\t@touch init-ran\n",
        encoding="utf-8",
    )

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "succeeded"
    assert (tmp_path / "init-ran").is_file()


@pytest.mark.parametrize("makefile", ["init:\n", "init: prerequisite\nprerequisite:\n"])
def test_real_make_structural_probe_accepts_init_without_recipe(tmp_path: Path, makefile: str) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(makefile, encoding="utf-8")

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "succeeded"
    assert result.command == ("make", "init")


def test_real_make_structural_probe_preserves_special_makefile_path(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    target = tmp_path / "repo $ # with space"
    target.mkdir()
    (target / "Makefile").write_text("init:\n\t@touch initialized\n", encoding="utf-8")

    result = MakeCliGateway().run_make_init_if_available(target)

    assert result.status == "succeeded"
    assert (target / "initialized").is_file()


def test_real_make_diagnostic_target_spoof_does_not_create_bootstrap(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(
        "$(info init:)\nall:\n\t@touch all-ran\n",
        encoding="utf-8",
    )

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "skipped"
    assert not (tmp_path / "all-ran").exists()


def test_real_make_error_text_spoof_is_detection_failure(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(
        "$(error No rule to make target 'init')\n",
        encoding="utf-8",
    )

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "detection_failed"
    assert result.command == ("make", "-n", "init")
    assert result.exit_code != 0


def test_real_make_missing_init_prerequisite_is_not_skipped(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(
        "init: missing-prerequisite\n\t@echo init\n",
        encoding="utf-8",
    )

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "detection_failed"
    assert result.exit_code != 0


def test_real_make_unrelated_default_failure_does_not_hide_init(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(
        "all: missing-default\ninit:\n\t@touch initialized\n",
        encoding="utf-8",
    )

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "succeeded"
    assert (tmp_path / "initialized").is_file()


def test_real_make_repository_default_rule_remains_available_for_init(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(
        ".DEFAULT:\n\t@touch $@\ninit: generated\n",
        encoding="utf-8",
    )

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "succeeded"
    assert (tmp_path / "generated").is_file()


def test_make_runner_start_failure_is_reported_as_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "Makefile").write_text("init:\n", encoding="utf-8")
    responses = [_completed(0, stdout="init:\n")]

    def runner(
        args: Sequence[str],
        *,
        cwd: Path,
        capture_output: bool,
        text: bool,
        check: bool,
        shell: bool,
    ) -> subprocess.CompletedProcess[str]:
        del cwd, capture_output, text, check, shell
        if args == ["make", "init"]:
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

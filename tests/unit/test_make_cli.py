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


def test_real_make_direct_detection_finds_init_after_long_prelude(tmp_path: Path) -> None:
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


@pytest.mark.parametrize("source", ["init.c", "init.o"])
def test_real_make_built_in_implicit_rule_is_not_bootstrap_authority(tmp_path: Path, source: str) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text("all:\n", encoding="utf-8")
    (tmp_path / source).write_text("int main(void) { return 0; }\n", encoding="utf-8")

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "skipped"
    assert result.command is None
    assert not (tmp_path / "init").exists()


def test_real_make_database_detection_resolves_dynamic_eval_target(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(
        "TARGET := init\n$(eval $(TARGET):; @touch initialized)\n",
        encoding="utf-8",
    )

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "succeeded"
    assert (tmp_path / "initialized").is_file()


@pytest.mark.parametrize("makefile", ["init:\n", "init: prerequisite\nprerequisite:\n"])
def test_real_make_direct_detection_accepts_init_without_recipe(tmp_path: Path, makefile: str) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(makefile, encoding="utf-8")

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "succeeded"
    assert result.command == ("make", "init")


def test_real_make_phony_init_is_present_and_executes(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(".PHONY: init\ninit:\n\t@touch initialized\n", encoding="utf-8")

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "succeeded"
    assert (tmp_path / "initialized").is_file()


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_real_make_existing_init_path_without_rule_is_skipped(tmp_path: Path, kind: str) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text("all:\n", encoding="utf-8")
    init_path = tmp_path / "init"
    if kind == "file":
        init_path.write_text("existing\n", encoding="utf-8")
    else:
        init_path.mkdir()

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "skipped"
    assert result.command is None
    assert init_path.exists()


def test_real_make_global_assignment_after_rule_without_init_is_skipped(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text("all:\nVAR = value\n", encoding="utf-8")

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "skipped"
    assert result.command is None


def test_real_make_default_rule_without_init_is_detection_failure(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(".DEFAULT:\n\t@echo default\n", encoding="utf-8")

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "detection_failed"
    assert result.exit_code == 0


def test_real_make_direct_detection_preserves_special_makefile_path(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    target = tmp_path / "repo $ # with space"
    target.mkdir()
    (target / "Makefile").write_text("init:\n\t@touch initialized\n", encoding="utf-8")

    result = MakeCliGateway().run_make_init_if_available(target)

    assert result.status == "succeeded"
    assert (target / "initialized").is_file()


def test_real_make_database_ignores_diagnostic_target_spoof(tmp_path: Path) -> None:
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


def test_real_make_malformed_phony_is_detection_failure(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(".PHONY: all: broken\n", encoding="utf-8")

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "detection_failed"
    assert result.command == ("make", "-n", "init")
    assert result.exit_code != 0


def test_real_make_database_probe_proves_no_init_without_source_scanning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "Makefile").write_text("all:\n\t@echo all\n", encoding="utf-8")
    calls: list[tuple[tuple[str, ...], Path]] = []

    def direct_only(make_executable: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(((make_executable, "-n", "init"), cwd))
        return _completed(2, stderr="No rule to make target 'init'.")

    def database_probe(make_executable: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(((make_executable, "-n", "-p", "-r", "-R", "init"), cwd))
        return _completed(0, stdout="# Files\nall:\n# Finished Make data base\n")

    monkeypatch.setattr("worktree_provisioner.infra.make_cli.shutil.which", lambda _: "/usr/bin/make")
    monkeypatch.setattr("worktree_provisioner.infra.make_cli._run_direct_detection", direct_only)
    monkeypatch.setattr("worktree_provisioner.infra.make_cli._run_database_probe", database_probe)

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "detection_failed"
    assert calls == [
        (("make", "-n", "init"), tmp_path),
        (("make", "-n", "-p", "-r", "-R", "init"), tmp_path),
    ]


def test_real_make_direct_failure_with_make_origin_change_is_not_skipped(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(
        "ifeq ($(origin MAKEOVERRIDES),undefined)\n$(error direct detection failed)\nendif\nall:\n",
        encoding="utf-8",
    )

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "detection_failed"
    assert result.exit_code != 0


def test_real_make_unrelated_implicit_pattern_does_not_hide_missing_init(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text("%.o: %.c\n\t@echo object\n", encoding="utf-8")

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "skipped"
    assert result.command is None


@pytest.mark.parametrize("name", ["MAKEFILES", "MAKEFLAGS", "MAKEOVERRIDES", "GNUMAKEFLAGS"])
def test_real_make_static_absence_refuses_authority_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text("all:\n", encoding="utf-8")
    monkeypatch.setenv(name, "--warn-undefined-variables")

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "detection_failed"
    assert result.exit_code is None


def test_real_make_makefiles_parse_time_sentinel_is_not_evaluated_before_precheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text("all:\n", encoding="utf-8")
    sentinel = tmp_path / "ambient-sentinel"
    injected = tmp_path / "injected.mk"
    injected.write_text(f"$(file >{sentinel},ambient)\n", encoding="utf-8")
    monkeypatch.setenv("MAKEFILES", str(injected))
    calls: list[tuple[str, ...]] = []

    def unexpected_make(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        del kwargs
        calls.append(tuple(args[0]))
        raise AssertionError("make must not start when authority environment is present")

    monkeypatch.setattr("worktree_provisioner.infra.make_cli._run_bounded_process", unexpected_make)

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "detection_failed"
    assert result.command == ("make", "-n", "init")
    assert result.exit_code is None
    assert calls == []
    assert not sentinel.exists()


@pytest.mark.parametrize(
    ("makefile", "expected_status"),
    [
        ("MAKEFLAGS += --warn-undefined-variables\nall:\n", "skipped"),
        ("MAKEOVERRIDES := inherited\nall:\n", "skipped"),
        ("ifeq ($(origin MAKEFLAGS),environment)\ninit:\nendif\n", "skipped"),
        ("define make-init\ninit: missing-prerequisite\nendef\n$(eval $(make-init))\n", "detection_failed"),
        ("%: missing-prerequisite\n\t@echo pattern\n", "detection_failed"),
        ("all: \\\n  other\nother:\n", "skipped"),
        ("this is not a valid make statement\n", "detection_failed"),
        ("./Makefile: missing-source\n\t@false\n", "detection_failed"),
    ],
)
def test_real_make_database_classifies_dynamic_or_ambiguous_makefiles(
    tmp_path: Path, makefile: str, expected_status: str
) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(makefile, encoding="utf-8")

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == expected_status
    if expected_status == "detection_failed":
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


def test_real_make_parse_like_rule_error_is_not_static_absence(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(
        "all: ; @echo all\nfoo: prerequisite: malformed\n",
        encoding="utf-8",
    )

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "detection_failed"
    assert result.exit_code != 0


@pytest.mark.parametrize(
    ("makefile", "expected_status"),
    [
        ("all:\nVAR=1\n\t@echo hi\n", "detection_failed"),
        ("all:\nVAR=1: all\n\t@echo hi\n", "detection_failed"),
        ("all: VAR=1\n", "skipped"),
    ],
)
def test_real_make_recipe_context_assignment_is_not_static_absence(
    tmp_path: Path, makefile: str, expected_status: str
) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(makefile, encoding="utf-8")

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == expected_status
    if expected_status == "detection_failed":
        assert result.exit_code != 0


def test_real_make_orphan_recipe_is_not_static_absence(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text("\t@echo orphan\nall:\n", encoding="utf-8")

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "detection_failed"
    assert result.exit_code != 0


def test_real_make_makefile_remake_failure_is_not_static_absence(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(
        "Makefile: missing-source\n\t@false\n",
        encoding="utf-8",
    )

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "detection_failed"
    assert result.exit_code != 0


def test_real_make_makefiles_injection_is_not_static_absence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text("all:\n", encoding="utf-8")
    injected = tmp_path / "injected.mk"
    injected.write_text("$(error injected source failure)\n", encoding="utf-8")
    monkeypatch.setenv("MAKEFILES", str(injected))

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "detection_failed"
    assert result.exit_code is None


def test_real_make_init_rule_selected_by_makecmdgoals_is_not_skipped(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(
        "ifneq (,$(filter init,$(MAKECMDGOALS)))\ninit: missing-prerequisite\n\t@echo init\nendif\n",
        encoding="utf-8",
    )

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "detection_failed"
    assert result.exit_code != 0


def test_real_make_init_conditional_include_failure_is_not_skipped(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(
        "ifeq ($(MAKECMDGOALS),init)\ninclude missing.mk\nendif\n",
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


def test_real_make_failing_repository_default_is_detection_failure(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    (tmp_path / "Makefile").write_text(
        ".DEFAULT:\n\t$(error repository-default-failure)\n",
        encoding="utf-8",
    )

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "detection_failed"
    assert result.exit_code != 0


def test_real_make_direct_detection_preserves_custom_recipe_prefix(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    makefile = ".RECIPEPREFIX := >\ninit:\n>@touch initialized\n"
    (tmp_path / "Makefile").write_text(makefile, encoding="utf-8")

    support = subprocess.run(
        ["make", "-n", "-f", "-", "init"],
        input=makefile,
        capture_output=True,
        text=True,
        check=False,
    )

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    if support.returncode != 0 and "missing separator" in support.stderr:
        # macOS GNU Make 3.81 predates .RECIPEPREFIX.  The important
        # invariant on unsupported versions is that parse failure is not
        # mistaken for a missing init target.
        assert result.status == "detection_failed"
        return
    assert result.status == "succeeded"
    assert (tmp_path / "initialized").is_file()


def test_real_make_direct_detection_preserves_symlinked_makefile_identity(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    real_makefile = tmp_path / "real.mk"
    real_makefile.write_text("init:\n\t@touch initialized\n", encoding="utf-8")
    (tmp_path / "Makefile").symlink_to(real_makefile.name)

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "succeeded"
    assert (tmp_path / "initialized").is_file()


def test_real_make_symlinked_makefile_preserves_cwd_relative_include(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    real_directory = tmp_path / "real"
    worktree = tmp_path / "worktree"
    real_directory.mkdir()
    worktree.mkdir()
    (real_directory / "Makefile").write_text("include commands.mk\n", encoding="utf-8")
    (worktree / "Makefile").symlink_to(real_directory / "Makefile")
    (worktree / "commands.mk").write_text("init:\n\t@touch initialized\n", encoding="utf-8")

    result = MakeCliGateway().run_make_init_if_available(worktree)

    assert result.status == "succeeded"
    assert (worktree / "initialized").is_file()


def test_real_make_direct_detection_preserves_relative_makefile_list_include(tmp_path: Path) -> None:
    if shutil.which("make") is None:
        pytest.skip("make is unavailable")
    config = tmp_path / "config"
    config.mkdir()
    (tmp_path / "Makefile").write_text("include config/targets.mk\n", encoding="utf-8")
    (config / "targets.mk").write_text(
        "include $(dir $(lastword $(MAKEFILE_LIST)))commands.mk\n",
        encoding="utf-8",
    )
    (config / "commands.mk").write_text("init:\n\t@touch initialized\n", encoding="utf-8")

    result = MakeCliGateway().run_make_init_if_available(tmp_path)

    assert result.status == "succeeded"
    assert (tmp_path / "initialized").is_file()


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

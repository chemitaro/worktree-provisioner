from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from worktree_provisioner.application.contracts import GitWorktreeRecord
from worktree_provisioner.application.ports import GitGateway
from worktree_provisioner.infra.git_cli import (
    GitAdapterError,
    GitCliGateway,
    parse_worktree_porcelain,
)


def test_parse_main_and_linked_records_preserves_porcelain_flags() -> None:
    output = """\
worktree /repo
HEAD 1111111111111111111111111111111111111111
branch refs/heads/main

worktree /worktrees/feature
HEAD 2222222222222222222222222222222222222222
branch refs/heads/feature

worktree /worktrees/detached
HEAD 3333333333333333333333333333333333333333
detached

worktree /repo.git
HEAD 4444444444444444444444444444444444444444
bare

worktree /worktrees/locked
HEAD 5555555555555555555555555555555555555555
branch refs/heads/locked
locked

worktree /worktrees/locked-with-reason
HEAD 6666666666666666666666666666666666666666
branch refs/heads/locked-reason
locked maintenance window
""".rstrip()

    records = parse_worktree_porcelain(output)

    assert records == [
        GitWorktreeRecord(
            path=Path("/repo"),
            head="1111111111111111111111111111111111111111",
            branch="main",
        ),
        GitWorktreeRecord(
            path=Path("/worktrees/feature"),
            head="2222222222222222222222222222222222222222",
            branch="feature",
        ),
        GitWorktreeRecord(
            path=Path("/worktrees/detached"),
            head="3333333333333333333333333333333333333333",
            branch=None,
            detached=True,
        ),
        GitWorktreeRecord(
            path=Path("/repo.git"),
            head="4444444444444444444444444444444444444444",
            branch=None,
            bare=True,
        ),
        GitWorktreeRecord(
            path=Path("/worktrees/locked"),
            head="5555555555555555555555555555555555555555",
            branch="locked",
            locked=True,
            lock_reason=None,
        ),
        GitWorktreeRecord(
            path=Path("/worktrees/locked-with-reason"),
            head="6666666666666666666666666666666666666666",
            branch="locked-reason",
            locked=True,
            lock_reason="maintenance window",
        ),
    ]


def test_parser_does_not_depend_on_final_blank_line() -> None:
    records = parse_worktree_porcelain("worktree /repo\nHEAD abc\nbranch refs/heads/main\n")

    assert records == [GitWorktreeRecord(path=Path("/repo"), head="abc", branch="main")]


def test_nul_parser_preserves_path_bytes_without_line_trimming() -> None:
    raw_path = '/tmp/ leading\ttrailing "quote" \\\\ 日本\n'
    output = f"worktree {raw_path}".encode() + b"\0" + b"HEAD abc\0branch refs/heads/feature\0\0"

    records = parse_worktree_porcelain(output)

    assert records == [GitWorktreeRecord(path=Path(raw_path), head="abc", branch="feature")]


@pytest.mark.parametrize("text", ["", "garbage\n", "HEAD abc\nbranch refs/heads/main\n"])
def test_parser_returns_no_fabricated_record_for_empty_or_malformed_output(text: str) -> None:
    assert parse_worktree_porcelain(text) == []


def test_git_gateway_conforms_to_protocol() -> None:
    gateway: GitGateway = GitCliGateway()
    assert isinstance(gateway, GitCliGateway)


def test_git_gateway_uses_exact_argv_and_preserves_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    add_path = tmp_path / "worktree path"
    remove_path = tmp_path / "remove path"
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        command = tuple(argv[1:])
        stdout = {
            ("rev-parse", "--show-toplevel"): f"{repo.resolve()}\n",
            ("rev-parse", "--abbrev-ref", "HEAD"): "main\n",
            ("worktree", "list", "--porcelain", "-z"): "worktree /repo\nHEAD abc\nbranch refs/heads/main\n",
        }.get(command, "")
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    monkeypatch.setattr("worktree_provisioner.infra.git_cli.shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("worktree_provisioner.infra.git_cli.subprocess.run", fake_run)
    gateway = GitCliGateway()

    assert gateway.resolve_checkout_root(repo) == repo.resolve()
    assert gateway.current_branch_or_none(repo) == "main"
    assert gateway.local_branch_exists(repo, "main") is True
    assert gateway.check_branch_ref(repo, "main") is True
    assert gateway.worktree_list(repo)[0].path == Path("/repo")
    gateway.add_worktree(repo, path=add_path, branch="feature")
    gateway.remove_worktree(repo, path=remove_path, force=False)
    gateway.remove_worktree(repo, path=remove_path, force=True)

    assert [argv for argv, _ in calls] == [
        ["git", "rev-parse", "--show-toplevel"],
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        ["git", "show-ref", "--verify", "--quiet", "refs/heads/main"],
        ["git", "check-ref-format", "--branch", "main"],
        ["git", "worktree", "list", "--porcelain", "-z"],
        ["git", "worktree", "add", "-b", "feature", str(add_path)],
        ["git", "worktree", "remove", str(remove_path)],
        ["git", "worktree", "remove", "--force", str(remove_path)],
    ]
    assert all(kwargs["shell"] is False for _, kwargs in calls)
    assert all(kwargs["cwd"] == repo for _, kwargs in calls)


def test_resolve_checkout_root_preserves_trailing_space_and_checks_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo "
    repo.mkdir()

    def fake_run(argv, **kwargs):
        assert kwargs["text"] is False
        return subprocess.CompletedProcess(argv, 0, stdout=os.fsencode(repo) + b"\n", stderr=b"")

    monkeypatch.setattr("worktree_provisioner.infra.git_cli.shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("worktree_provisioner.infra.git_cli.subprocess.run", fake_run)

    assert GitCliGateway().resolve_checkout_root(repo) == repo.resolve()


def test_resolve_checkout_root_rejects_wrong_repository_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    wrong = tmp_path / "wrong"
    repo.mkdir()
    wrong.mkdir()

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=os.fsencode(wrong) + b"\n", stderr=b"")

    monkeypatch.setattr("worktree_provisioner.infra.git_cli.shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("worktree_provisioner.infra.git_cli.subprocess.run", fake_run)

    with pytest.raises(GitAdapterError, match="outside the requested checkout"):
        GitCliGateway().resolve_checkout_root(repo)


def test_resolve_checkout_root_rejects_empty_delimited_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=b"\n", stderr=b"")

    monkeypatch.setattr("worktree_provisioner.infra.git_cli.shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("worktree_provisioner.infra.git_cli.subprocess.run", fake_run)

    with pytest.raises(GitAdapterError, match="empty checkout root"):
        GitCliGateway().resolve_checkout_root(repo)


def test_resolve_checkout_root_accepts_repository_subdirectory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    subdirectory = repo / "nested"
    subdirectory.mkdir(parents=True)

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=os.fsencode(repo) + b"\n", stderr=b"")

    monkeypatch.setattr("worktree_provisioner.infra.git_cli.shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("worktree_provisioner.infra.git_cli.subprocess.run", fake_run)

    assert GitCliGateway().resolve_checkout_root(subdirectory) == repo.resolve()


def test_real_git_trailing_space_repository_mutation_boundary(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is unavailable")
    (tmp_path / "repo").mkdir()
    repo = tmp_path / "repo "
    linked = tmp_path / "work tree"
    repo.mkdir()

    def run_git(*args: str) -> None:
        subprocess.run(
            [git, *args],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        )

    run_git("init", "--quiet")
    run_git("config", "user.email", "test@example.invalid")
    run_git("config", "user.name", "Worktree Provisioner Test")
    (repo / "README").write_text("fixture\n", encoding="utf-8")
    run_git("add", "README")
    run_git("commit", "--quiet", "-m", "fixture")

    gateway = GitCliGateway()
    assert gateway.resolve_checkout_root(repo) == repo.resolve()
    nested = repo / "nested"
    nested.mkdir()
    assert gateway.resolve_checkout_root(nested) == repo.resolve()
    assert any(record.path == repo.resolve() for record in gateway.worktree_list(repo))

    gateway.add_worktree(repo, path=linked, branch="fixture-linked")
    assert gateway.resolve_checkout_root(linked) == linked.resolve()
    assert any(record.path == linked.resolve() for record in gateway.worktree_list(repo))

    gateway.remove_worktree(repo, path=linked, force=False)
    assert not linked.exists()
    assert all(record.path != linked.resolve() for record in gateway.worktree_list(repo))


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        ("fatal: a branch named 'feature' already exists\n", "branch"),
        ("fatal: '/tmp/worktree' already exists\n", "path"),
        ("fatal: 'feature' is already used by worktree at '/tmp/other'\n", "checked_out"),
        ("fatal: 'feature' is already checked out at '/tmp/other'\n", "checked_out"),
        ("fatal: ref lock already exists\n", None),
    ],
)
def test_add_worktree_exposes_only_operation_specific_collision_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stderr: str, expected: str | None
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = Path("/tmp/worktree")

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 128, stdout="", stderr=stderr)

    monkeypatch.setattr("worktree_provisioner.infra.git_cli.shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("worktree_provisioner.infra.git_cli.subprocess.run", fake_run)

    with pytest.raises(GitAdapterError) as caught:
        GitCliGateway().add_worktree(repo, path=target, branch="feature")

    assert caught.value.collision_kind == expected


def test_branch_checks_return_false_for_expected_negative_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="not found")

    monkeypatch.setattr("worktree_provisioner.infra.git_cli.shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("worktree_provisioner.infra.git_cli.subprocess.run", fake_run)
    gateway = GitCliGateway()

    assert gateway.local_branch_exists(repo, "missing") is False
    assert gateway.check_branch_ref(repo, "invalid") is False


@pytest.mark.parametrize("method_name", ["local_branch_exists", "check_branch_ref"])
def test_branch_checks_raise_for_repository_failure_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method_name: str
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 128, stdout="", stderr="fatal: not a repository")

    monkeypatch.setattr("worktree_provisioner.infra.git_cli.shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("worktree_provisioner.infra.git_cli.subprocess.run", fake_run)

    with pytest.raises(GitAdapterError) as caught:
        getattr(GitCliGateway(), method_name)(repo, "main")

    assert caught.value.returncode == 128
    assert caught.value.operation == method_name
    assert "fatal: not a repository" in caught.value.diagnostic


def test_missing_git_is_typed_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr("worktree_provisioner.infra.git_cli.shutil.which", lambda _: None)

    with pytest.raises(GitAdapterError) as caught:
        GitCliGateway().worktree_list(repo)

    assert caught.value.returncode is None
    assert caught.value.operation == "worktree_list"
    assert caught.value.argv == ("git", "worktree", "list", "--porcelain", "-z")


def test_oserror_and_command_output_are_typed_and_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr("worktree_provisioner.infra.git_cli.shutil.which", lambda _: "/usr/bin/git")

    def raise_oserror(*args, **kwargs):
        raise OSError("exec failed")

    monkeypatch.setattr("worktree_provisioner.infra.git_cli.subprocess.run", raise_oserror)
    with pytest.raises(GitAdapterError) as caught_oserror:
        GitCliGateway().worktree_list(repo)
    assert "exec failed" in caught_oserror.value.diagnostic

    def fail_with_output(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 17, stdout="out" * 3000, stderr="err" * 3000)

    monkeypatch.setattr("worktree_provisioner.infra.git_cli.subprocess.run", fail_with_output)
    with pytest.raises(GitAdapterError) as caught_command:
        GitCliGateway().worktree_list(repo)
    assert caught_command.value.returncode == 17
    assert len(caught_command.value.diagnostic) <= 4096
    assert "stderr:" in caught_command.value.diagnostic
    assert "stdout:" in caught_command.value.diagnostic


@pytest.mark.parametrize("path", [Path("missing"), Path("file")])
def test_repository_path_validation_happens_before_git(
    tmp_path: Path, path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if path.name == "file":
        (tmp_path / path).write_text("not a repo", encoding="utf-8")
    monkeypatch.setattr(
        "worktree_provisioner.infra.git_cli.subprocess.run",
        lambda *args, **kwargs: pytest.fail("git must not run for an invalid repository path"),
    )

    with pytest.raises(GitAdapterError):
        GitCliGateway().worktree_list(tmp_path / path)

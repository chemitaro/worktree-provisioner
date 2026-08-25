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
    _git_environment,
    _validated_relative_name,
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


def test_nul_parser_restores_valid_utf8_fields_and_preserves_raw_bytes() -> None:
    raw_path = b"/tmp/repo-\xe6\x97\xa5-\xff"
    output = b"worktree " + raw_path + b"\0HEAD abc\0branch refs/heads/\xe6\xa9\x9f\xe8\x83\xbd\0\0"

    records = parse_worktree_porcelain(output)

    assert records == [
        GitWorktreeRecord(
            path=Path("/tmp/repo-日-\udcff"),
            head="abc",
            branch="機能",
        )
    ]


def test_nul_parser_restores_mixed_utf8_and_raw_bytes_under_ascii_fsdecode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = b"/tmp/repo-\xe6\x97\xa5-\xff"
    output = b"worktree " + raw_path + b"\0HEAD abc\0branch refs/heads/\xe6\xa9\x9f\xe8\x83\xbd\0\0"

    monkeypatch.setattr(
        "worktree_provisioner.infra.git_cli.os.fsdecode",
        lambda value: value.decode("ascii", errors="surrogateescape"),
    )

    records = parse_worktree_porcelain(output)

    assert records[0].path == Path(raw_path.decode("ascii", errors="surrogateescape"))
    assert records[0].branch == "機能"


@pytest.mark.parametrize("text", ["", "garbage\n", "HEAD abc\nbranch refs/heads/main\n"])
def test_parser_returns_no_fabricated_record_for_empty_or_malformed_output(text: str) -> None:
    assert parse_worktree_porcelain(text) == []


def test_git_gateway_conforms_to_protocol() -> None:
    gateway: GitGateway = GitCliGateway()
    assert isinstance(gateway, GitCliGateway)


def test_git_environment_removes_repository_authority_but_preserves_user_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = {
        "GIT_DIR": "/repo-b/.git",
        "GIT_WORK_TREE": "/repo-b",
        "GIT_COMMON_DIR": "/repo-b/.git",
        "GIT_OBJECT_DIRECTORY": "/repo-b/.git/objects",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/repo-b/.git/objects",
        "GIT_INDEX_FILE": "/repo-b/.git/index",
        "GIT_GRAFT_FILE": "/repo-b/.git/info/grafts",
        "GIT_SHALLOW_FILE": "/repo-b/.git/shallow",
        "GIT_NAMESPACE": "other",
        "GIT_PREFIX": "other/",
        "GIT_INTERNAL_SUPER_PREFIX": "other/",
        "GIT_IMPLICIT_WORK_TREE": "0",
        "GIT_CEILING_DIRECTORIES": "/repo-b",
        "GIT_DISCOVERY_ACROSS_FILESYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "0",
        "GIT_REPLACE_REF_BASE": "refs/replace/other/",
        "GIT_QUARANTINE_PATH": "/repo-b/.git/objects/incoming",
        "GIT_CONFIG": "/repo-b/config",
        "GIT_CONFIG_SYSTEM": "/repo-b/system-config",
        "GIT_CONFIG_GLOBAL": "/repo-b/global-config",
        "GIT_CONFIG_XDG": "/repo-b/xdg-config",
        "GIT_CONFIG_NOSYSTEM": "0",
        "GIT_CONFIG_DISABLE": "0",
        "GIT_CONFIG_ENVIRONMENT": "/repo-b/environment-config",
        "GIT_CONFIG_EXTENSIONS": "other",
        "GIT_CONFIG_PARAMETERS": "'core.repositoryformatversion'='99'",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.repositoryformatversion",
        "GIT_CONFIG_VALUE_0": "99",
    }
    for key, value in authority.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("GIT_PAGER", "less")
    monkeypatch.setenv("GIT_EDITOR", "vi")

    environment = _git_environment()

    assert all(key not in environment for key in authority)
    assert environment["GIT_PAGER"] == "less"
    assert environment["GIT_EDITOR"] == "vi"
    assert environment["LC_ALL"] == "C"


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
        ["git", "worktree", "add", "-b", "feature", "--", str(add_path)],
        ["git", "worktree", "remove", "--", str(remove_path)],
        ["git", "worktree", "remove", "--force", "--", str(remove_path)],
    ]
    assert all(kwargs["shell"] is False for _, kwargs in calls)
    assert all(kwargs["cwd"] == repo for _, kwargs in calls)
    text_calls = [kwargs for argv, kwargs in calls if kwargs["text"] is True]
    assert all(kwargs["encoding"] == "utf-8" for kwargs in text_calls)
    assert all(kwargs["errors"] == "surrogateescape" for kwargs in text_calls)


@pytest.mark.parametrize("name", ["nested/worktree", "-leading-worktree", "worktree with spaces", "repo\\worktree"])
def test_bound_worktree_name_preserves_relative_posix_path_and_backslash(name: str) -> None:
    assert _validated_relative_name(name) == name


@pytest.mark.parametrize("name", ["", ".", "..", "/absolute", "nested/./worktree", "nested/../worktree", "nul\x00name"])
def test_bound_worktree_name_rejects_absolute_empty_nul_and_traversal(name: str) -> None:
    with pytest.raises(GitAdapterError):
        _validated_relative_name(name)


def test_git_gateway_encodes_unicode_argv_when_filesystem_locale_is_ascii(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    calls: list[list[str | bytes]] = []

    def fake_fsencode(value: str) -> bytes:
        if any(ord(character) > 127 for character in value):
            raise UnicodeEncodeError("ascii", value, 0, len(value), "non-ASCII fixture")
        return value.encode("ascii")

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr("worktree_provisioner.infra.git_cli.shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("worktree_provisioner.infra.git_cli.os.fsencode", fake_fsencode)
    monkeypatch.setattr("worktree_provisioner.infra.git_cli.subprocess.run", fake_run)

    GitCliGateway().add_worktree(repo, path=tmp_path / "target", branch="feature-日本")

    assert calls[0][4] == "feature-日本".encode()


def test_current_branch_restores_valid_utf8_surrogates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    surrogate_branch = b"\xe6\xa9\x9f\xe8\x83\xbd".decode("ascii", errors="surrogateescape")

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=surrogate_branch + "\n", stderr="")

    monkeypatch.setattr("worktree_provisioner.infra.git_cli.shutil.which", lambda _: "/usr/bin/git")
    monkeypatch.setattr("worktree_provisioner.infra.git_cli.subprocess.run", fake_run)

    assert GitCliGateway().current_branch_or_none(repo) == "機能"


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


def test_real_git_repository_authority_environment_cannot_redirect_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is unavailable")
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"

    def init_repo(repo: Path, marker: str) -> None:
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
        (repo / "README").write_text(f"{marker}\n", encoding="utf-8")
        run_git("add", "README")
        run_git("commit", "--quiet", "-m", marker)

    init_repo(repo_a, "repo-a")
    init_repo(repo_b, "repo-b")
    linked = tmp_path / "repo-a-linked"
    authority = {
        "GIT_DIR": str(repo_b / ".git"),
        "GIT_WORK_TREE": str(repo_b),
        "GIT_COMMON_DIR": str(repo_b / ".git"),
        "GIT_OBJECT_DIRECTORY": str(repo_b / ".git" / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(repo_b / ".git" / "objects"),
        "GIT_INDEX_FILE": str(repo_b / ".git" / "index"),
        "GIT_GRAFT_FILE": str(repo_b / ".git" / "info" / "grafts"),
        "GIT_SHALLOW_FILE": str(repo_b / ".git" / "shallow"),
        "GIT_NAMESPACE": "other",
        "GIT_PREFIX": "other/",
        "GIT_INTERNAL_SUPER_PREFIX": "other/",
        "GIT_IMPLICIT_WORK_TREE": "0",
        "GIT_CEILING_DIRECTORIES": str(repo_b),
        "GIT_DISCOVERY_ACROSS_FILESYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "0",
        "GIT_REPLACE_REF_BASE": "refs/replace/other/",
        "GIT_QUARANTINE_PATH": str(repo_b / ".git" / "objects" / "incoming"),
        "GIT_CONFIG": str(repo_b / "config"),
        "GIT_CONFIG_SYSTEM": str(repo_b / "system-config"),
        "GIT_CONFIG_GLOBAL": str(repo_b / "global-config"),
        "GIT_CONFIG_XDG": str(repo_b / "xdg-config"),
        "GIT_CONFIG_NOSYSTEM": "0",
        "GIT_CONFIG_DISABLE": "0",
        "GIT_CONFIG_ENVIRONMENT": str(repo_b / "environment-config"),
        "GIT_CONFIG_EXTENSIONS": "other",
        "GIT_CONFIG_PARAMETERS": "'core.repositoryformatversion'='99'",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.repositoryformatversion",
        "GIT_CONFIG_VALUE_0": "99",
    }
    for key, value in authority.items():
        monkeypatch.setenv(key, value)

    gateway = GitCliGateway()
    assert gateway.resolve_checkout_root(repo_a) == repo_a.resolve()
    assert [record.path for record in gateway.worktree_list(repo_a)] == [repo_a.resolve()]

    gateway.add_worktree(repo_a, path=linked, branch="repo-a-linked")
    assert linked.is_dir()
    assert any(record.path == linked.resolve() for record in gateway.worktree_list(repo_a))
    assert all(record.path != linked.resolve() for record in gateway.worktree_list(repo_b))

    gateway.remove_worktree(repo_a, path=linked, force=False)
    assert not linked.exists()
    assert [record.path for record in gateway.worktree_list(repo_b)] == [repo_b.resolve()]


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

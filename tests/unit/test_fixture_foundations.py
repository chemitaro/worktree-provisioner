from __future__ import annotations

from pathlib import Path

from conftest import FakeBootstrapGateway, FakeFilesystemGateway, FakeGitGateway


def test_fake_git_gateway_records_exact_remove_force_call(fake_git_gateway: FakeGitGateway) -> None:
    target = fake_git_gateway.checkout_root / "managed-target"
    fake_git_gateway.remove_worktree(fake_git_gateway.checkout_root, path=target, force=True)

    name, args, kwargs = fake_git_gateway.calls[-1]
    assert name == "remove_worktree"
    assert args == (fake_git_gateway.checkout_root,)
    assert kwargs == {"path": target, "force": True}


def test_fake_bootstrap_gateway_records_created_worktree(
    fake_bootstrap_gateway: FakeBootstrapGateway, tmp_path: Path
) -> None:
    target = tmp_path / "created"
    result = fake_bootstrap_gateway.run_make_init_if_available(target)

    assert fake_bootstrap_gateway.calls == [target]
    assert result.status == "skipped"


def test_fake_filesystem_gateway_is_no_follow_and_target_only(
    fake_filesystem_gateway: FakeFilesystemGateway, tmp_path: Path
) -> None:
    target = tmp_path / "target"
    fake_filesystem_gateway.kinds[target] = "symlink"
    fake_filesystem_gateway.existing.add(target)

    assert fake_filesystem_gateway.lstat_kind(target) == "symlink"
    assert fake_filesystem_gateway.path_exists_no_follow(target)
    fake_filesystem_gateway.remove_target_no_follow(target)
    assert not fake_filesystem_gateway.path_exists_no_follow(target)
    assert [name for name, _ in fake_filesystem_gateway.calls] == [
        "lstat_kind",
        "path_exists_no_follow",
        "remove_target_no_follow",
        "path_exists_no_follow",
    ]

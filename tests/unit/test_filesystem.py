from __future__ import annotations

import os
from pathlib import Path

import pytest

from worktree_provisioner.infra.filesystem import FilesystemAdapterError, FilesystemCliGateway


def test_lstat_kind_and_no_follow_existence_cover_normal_and_broken_symlink(tmp_path: Path) -> None:
    gateway = FilesystemCliGateway()
    directory = tmp_path / "directory"
    regular = tmp_path / "regular"
    link = tmp_path / "link"
    broken = tmp_path / "broken"
    directory.mkdir()
    regular.write_text("data", encoding="utf-8")
    link.symlink_to(directory, target_is_directory=True)
    broken.symlink_to(tmp_path / "missing")

    assert gateway.lstat_kind(directory) == "directory"
    assert gateway.lstat_kind(regular) == "file"
    assert gateway.lstat_kind(link) == "symlink"
    assert gateway.lstat_kind(broken) == "symlink"
    assert gateway.lstat_kind(tmp_path / "missing") == "missing"
    assert gateway.path_exists_no_follow(link) is True
    assert gateway.path_exists_no_follow(broken) is True
    assert gateway.path_exists_no_follow(tmp_path / "missing") is False


def test_ensure_directory_is_idempotent_for_actual_directory(tmp_path: Path) -> None:
    gateway = FilesystemCliGateway()
    target = tmp_path / "nested" / "namespace"

    gateway.ensure_directory(target)
    gateway.ensure_directory(target)

    assert target.is_dir()
    assert gateway.lstat_kind(target) == "directory"


def test_open_directory_resolves_ancestor_symlink_but_rejects_leaf_symlink(tmp_path: Path) -> None:
    gateway = FilesystemCliGateway()
    actual = tmp_path / "actual"
    leaf = actual / "leaf"
    actual.mkdir()
    leaf.mkdir()
    ancestor_link = tmp_path / "ancestor-link"
    ancestor_link.symlink_to(actual, target_is_directory=True)

    with gateway.open_directory(ancestor_link / "leaf") as handle:
        assert handle.path == leaf.resolve()

    with pytest.raises(FilesystemAdapterError):
        gateway.open_directory(ancestor_link)


@pytest.mark.parametrize("kind", ["file", "symlink"])
def test_ensure_directory_rejects_non_directory_and_namespace_symlink(tmp_path: Path, kind: str) -> None:
    gateway = FilesystemCliGateway()
    target = tmp_path / "namespace"
    if kind == "file":
        target.write_text("not a directory", encoding="utf-8")
    else:
        target.symlink_to(tmp_path, target_is_directory=True)

    with pytest.raises(FilesystemAdapterError) as caught:
        gateway.ensure_directory(target)

    assert caught.value.operation == "ensure_directory"
    assert caught.value.kind == kind


@pytest.mark.parametrize("target_kind", ["directory", "symlink", "broken", "file"])
def test_remove_target_no_follow_removes_only_the_target(tmp_path: Path, target_kind: str) -> None:
    gateway = FilesystemCliGateway()
    target = tmp_path / target_kind
    external = tmp_path / "external"
    external.mkdir()
    if target_kind == "directory":
        target.mkdir()
        (target / "content").write_text("keep", encoding="utf-8")
    elif target_kind == "file":
        target.write_text("delete", encoding="utf-8")
    elif target_kind == "symlink":
        target.symlink_to(external, target_is_directory=True)
    else:
        target.symlink_to(tmp_path / "not-present")

    gateway.remove_target_no_follow(target)

    assert not os.path.lexists(target)
    assert external.is_dir()


def test_special_file_is_rejected(tmp_path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFO is not available on this platform")
    target = tmp_path / "fifo"
    os.mkfifo(target)

    with pytest.raises(FilesystemAdapterError) as caught:
        FilesystemCliGateway().remove_target_no_follow(target)

    assert caught.value.kind == "other"
    assert target.exists()


def test_missing_cleanup_target_is_typed_failure(tmp_path: Path) -> None:
    with pytest.raises(FilesystemAdapterError) as caught:
        FilesystemCliGateway().remove_target_no_follow(tmp_path / "missing")

    assert caught.value.kind == "missing"


def test_lstat_permission_or_race_errors_are_typed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    gateway = FilesystemCliGateway()
    target = tmp_path / "target"

    def raise_permission(self):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "lstat", raise_permission)
    with pytest.raises(FilesystemAdapterError) as caught:
        gateway.lstat_kind(target)

    assert caught.value.operation == "lstat_kind"
    assert "denied" in str(caught.value)

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import FakeFilesystemGateway  # type: ignore[import-not-found]

from worktree_provisioner.application.root_and_naming import (
    LabelValidationError,
    RootResolutionError,
    candidate_id,
    candidate_index,
    make_candidate,
    normalize_label,
    select_root,
    validate_namespace,
    validate_root,
)
from worktree_provisioner.infra.filesystem import FilesystemCliGateway


class MappingEnvironment:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.requested: list[str] = []

    def getenv(self, name: str) -> str | None:
        self.requested.append(name)
        return self.values.get(name)


def test_root_selection_uses_explicit_value_without_lower_precedence_fallback(tmp_path: Path) -> None:
    environment = MappingEnvironment({"WORKTREE_PROVISIONER_ROOT": str(tmp_path / "env")})

    with pytest.raises(RootResolutionError) as caught:
        select_root("", environment)

    assert caught.value.code == "root_required"
    assert environment.requested == []


def test_legacy_environment_is_not_queried() -> None:
    environment = MappingEnvironment({"SPEC_DOCK_WORKTREE_ROOT": "/tmp/legacy"})

    with pytest.raises(RootResolutionError) as caught:
        select_root(None, environment)

    assert caught.value.code == "root_required"
    assert environment.requested == ["WORKTREE_PROVISIONER_ROOT"]


def test_broken_symlink_root_is_rejected_before_missing_root_creation(tmp_path: Path, symlink_capability: Path) -> None:
    del symlink_capability
    lexical_root = tmp_path / "broken-root"
    lexical_root.symlink_to(tmp_path / "does-not-exist")
    filesystem = FilesystemCliGateway()

    selected = select_root(lexical_root, MappingEnvironment({}))
    with pytest.raises(RootResolutionError) as caught:
        validate_root(selected, filesystem)

    assert caught.value.code == "invalid_root"
    assert lexical_root.is_symlink()


def test_directory_symlink_root_is_allowed_and_canonicalized(tmp_path: Path) -> None:
    target = tmp_path / "real-root"
    target.mkdir()
    lexical_root = tmp_path / "root-link"
    lexical_root.symlink_to(target, target_is_directory=True)
    filesystem = FilesystemCliGateway()

    selected = select_root(lexical_root, MappingEnvironment({}))
    assert validate_root(selected, filesystem) == target


@pytest.mark.parametrize(
    ("label", "expected"),
    [(None, "wt1"), ("issue-369", "issue-369"), ("issue-369", "issue-3692")],
)
def test_candidate_ids_are_deterministic(label: str | None, expected: str) -> None:
    index = 1 if expected in {"wt1", "issue-369"} else 2
    assert candidate_id(label, index) == expected
    assert candidate_index(label, expected) == index


@pytest.mark.parametrize("label", ["", "Issue", "issue_1", "issue/1", "issue.1", "issue 1"])
def test_label_grammar_is_fail_closed(label: str) -> None:
    with pytest.raises(LabelValidationError):
        normalize_label(label)


def test_candidate_keeps_slash_in_invocation_branch_and_uses_main_namespace() -> None:
    candidate = make_candidate(
        label="setup",
        index=1,
        namespace=Path("/managed/example"),
        repo_basename="example",
        branch_prefix="feature/current",
    )

    assert candidate.path == Path("/managed/example/example-setup")
    assert candidate.branch == "feature/current-setup"


def test_namespace_symlink_is_rejected_without_following_target(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    namespace = root / "example"
    namespace.symlink_to(target, target_is_directory=True)
    filesystem = FilesystemCliGateway()

    with pytest.raises(RootResolutionError) as caught:
        validate_namespace(root, "example", filesystem)

    assert caught.value.code == "unsafe_namespace"
    assert target.is_dir()


def test_fake_filesystem_foundation_remains_usable(fake_filesystem_gateway: FakeFilesystemGateway) -> None:
    target = Path("/tmp/namespace")
    fake_filesystem_gateway.ensure_directory(target)
    assert fake_filesystem_gateway.path_exists_no_follow(target)

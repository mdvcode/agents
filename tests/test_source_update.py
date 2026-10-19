from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_harness import cli
from ai_harness.source_update import SourceStateError, inspect_source_changes


def git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


@pytest.fixture
def source_pair(tmp_path: Path) -> tuple[Path, Path]:
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-b", "main")
    git(upstream, "config", "user.name", "Test")
    git(upstream, "config", "user.email", "test@example.com")
    (upstream / "docs/memory").mkdir(parents=True)
    (upstream / "docs/memory/lessons_learned.md").write_text("base knowledge\n")
    (upstream / "scripts").mkdir()
    (upstream / "scripts/example.py").write_text("version = 1\n")
    (upstream / ".gitignore").write_text(".agent-runs/\n")
    git(upstream, "add", ".")
    git(upstream, "commit", "-m", "initial")
    source = tmp_path / "downloaded-system"
    subprocess.run(
        ["git", "clone", str(upstream), str(source)], check=True, capture_output=True
    )
    return upstream, source


def advance(upstream: Path, *, memory: bool = False) -> None:
    (upstream / "scripts/example.py").write_text("version = 2\n")
    if memory:
        (upstream / "docs/memory/lessons_learned.md").write_text("upstream knowledge\n")
    git(upstream, "add", ".")
    git(upstream, "commit", "-m", "update")


@pytest.mark.parametrize("upstream_memory_changes", [False, True])
def test_update_preserves_memory_and_nested_projects(
    source_pair: tuple[Path, Path], upstream_memory_changes: bool
) -> None:
    upstream, source = source_pair
    memory = source / "docs/memory/lessons_learned.md"
    memory.write_bytes(b"private local knowledge\r\n")
    memory.chmod(0o640)
    for name in (
        ".pnpm-store/cache",
        ".worktrees/nested",
        "outputs/result",
        "review-agent/AGENTS.md",
    ):
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("keep this local file\n")
    nested = source / "review-agent"
    git(nested, "init", "-b", "custom-default")
    advance(upstream, memory=upstream_memory_changes)
    backup = cli.refresh_git_source(source)
    assert backup is not None
    assert git(source, "rev-parse", "HEAD") == git(upstream, "rev-parse", "HEAD")
    assert (source / "scripts/example.py").read_text() == "version = 2\n"
    assert memory.read_bytes() == b"private local knowledge\r\n"
    assert memory.stat().st_mode & 0o777 == 0o640
    assert (
        backup / "local/docs/memory/lessons_learned.md"
    ).read_bytes() == memory.read_bytes()
    expected_upstream = (
        b"upstream knowledge\n" if upstream_memory_changes else b"base knowledge\n"
    )
    assert (
        backup / "upstream/docs/memory/lessons_learned.md"
    ).read_bytes() == expected_upstream
    assert git(nested, "symbolic-ref", "--short", "HEAD") == "custom-default"
    for name in (
        ".pnpm-store/cache",
        ".worktrees/nested",
        "outputs/result",
        "review-agent/AGENTS.md",
    ):
        assert (source / name).read_text() == "keep this local file\n"
    assert git(source, "diff", "--cached") == ""


def test_failed_pull_restores_private_memory(source_pair: tuple[Path, Path]) -> None:
    _, source = source_pair
    memory = source / "docs/memory/lessons_learned.md"
    memory.write_text("my local memory\n")
    git(source, "remote", "set-url", "origin", str(source / "missing-remote"))
    before = git(source, "rev-parse", "HEAD")
    with pytest.raises(cli.CLIError, match="downloading the update failed"):
        cli.refresh_git_source(source)
    assert memory.read_text() == "my local memory\n"
    assert git(source, "rev-parse", "HEAD") == before
    assert (
        len(
            list(
                (source / ".agent-runs").glob(
                    "update-*/local/docs/memory/lessons_learned.md"
                )
            )
        )
        == 1
    )


@pytest.mark.parametrize(
    "relative,staged",
    [("scripts/example.py", False), ("docs/memory/lessons_learned.md", True)],
)
def test_local_code_and_staged_state_are_not_overwritten(
    source_pair: tuple[Path, Path], relative: str, staged: bool
) -> None:
    upstream, source = source_pair
    (source / relative).write_text("user modification\n")
    if staged:
        git(source, "add", relative)
    before = git(source, "status", "--porcelain")
    advance(upstream)
    with pytest.raises(cli.CLIError, match="local source or staged changes"):
        cli.refresh_git_source(source)
    assert (source / relative).read_text() == "user modification\n"
    assert git(source, "status", "--porcelain") == before
    assert not (source / ".agent-runs").exists()


def test_untracked_package_code_still_requires_review(
    source_pair: tuple[Path, Path],
) -> None:
    _, source = source_pair
    (source / "scripts/new.py").write_text("user code\n")
    with pytest.raises(SourceStateError, match="scripts/new.py"):
        inspect_source_changes(source, cli.update_process)


def test_symlinked_memory_is_never_followed(
    source_pair: tuple[Path, Path], tmp_path: Path
) -> None:
    _, source = source_pair
    memory = source / "docs/memory/lessons_learned.md"
    memory.unlink()
    outside = tmp_path / "outside"
    outside.write_text("untouched\n")
    memory.symlink_to(outside)
    with pytest.raises(cli.CLIError):
        cli.refresh_git_source(source)
    assert outside.read_text() == "untouched\n"


def test_project_registry_is_preserved_as_private_configuration(
    source_pair: tuple[Path, Path],
) -> None:
    upstream, source = source_pair
    for target in (upstream, source):
        (target / ".agent-repositories.yaml").write_text(
            "version: 1\nrepositories: {}\n"
        )
    git(upstream, "add", ".agent-repositories.yaml")
    git(upstream, "commit", "-m", "registry defaults")
    # Remove the untracked local copy before receiving its initial tracked default.
    (source / ".agent-repositories.yaml").unlink()
    git(source, "pull", "--ff-only")
    registry = source / ".agent-repositories.yaml"
    registry.write_text("version: 1\nrepositories: {private-project: {}}\n")
    advance(upstream)
    backup = cli.refresh_git_source(source)
    assert backup is not None
    assert registry.read_text() == "version: 1\nrepositories: {private-project: {}}\n"


def test_reviewed_revision_is_only_pulled_when_explicitly_selected(
    source_pair: tuple[Path, Path],
) -> None:
    upstream, source = source_pair
    git(upstream, "switch", "-c", "fix/reviewed-update")
    advance(upstream)
    cli.refresh_git_source(source, "fix/reviewed-update")
    assert git(source, "rev-parse", "HEAD") == git(upstream, "rev-parse", "HEAD")
    assert git(source, "branch", "--show-current") == "main"

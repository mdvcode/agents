from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ai_harness import cli
from ai_harness.local_setup import AGENTS_TEMPLATE, keep_generated_setup_local
from ai_harness.project import ProjectConfigError, load_project_config
from scripts.worktree_manager import prepare_task_branch


def git(path: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=path, check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("AI_HARNESS_CONFIG_HOME", str(tmp_path / "config"))
    path = tmp_path / "project"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    (path / "README.md").write_text("base\n")
    git(path, "add", "README.md")
    git(path, "commit", "-m", "base")
    return path


def test_first_task_branch_needs_no_setup_commit(repository: Path) -> None:
    exclude = repository / ".git/info/exclude"
    original = b"# Preserve local rules without a trailing newline\n/personal.txt"
    exclude.write_bytes(original)
    head = git(repository, "rev-parse", "HEAD")
    assert cli.main(["init", "--repo", str(repository)]) == 0
    config = load_project_config(repository)
    assert git(repository, "status", "--porcelain") == ""
    assert exclude.read_bytes().startswith(original + b"\n")
    assert not (repository / ".gitignore").exists()
    repaired = exclude.read_bytes()
    keep_generated_setup_local(config)
    assert exclude.read_bytes() == repaired
    result = prepare_task_branch(repository, "feat/first-task", "main")
    assert result["execution_status"] == "completed"
    assert git(repository, "rev-parse", "HEAD") == head
    assert (repository / "AGENTS.md").read_text() == AGENTS_TEMPLATE
    (repository / ".agent/notes.md").write_text("user notes\n")
    assert ".agent/" in git(repository, "status", "--porcelain")


def test_existing_custom_instructions_remain_visible(repository: Path) -> None:
    (repository / "AGENTS.md").write_text("# Custom project rules\n")
    assert cli.main(["init", "--repo", str(repository)]) == 0
    assert git(repository, "status", "--porcelain") == "?? AGENTS.md"
    result = prepare_task_branch(repository, "feat/custom", "main")
    assert result["execution_status"] == "blocked"
    assert (repository / "AGENTS.md").read_text() == "# Custom project rules\n"


def test_tracked_setup_changes_remain_visible(repository: Path) -> None:
    (repository / "AGENTS.md").write_text(AGENTS_TEMPLATE)
    git(repository, "add", "AGENTS.md")
    git(repository, "commit", "-m", "project instructions")
    assert cli.main(["init", "--repo", str(repository)]) == 0
    (repository / "AGENTS.md").write_text(AGENTS_TEMPLATE + "\nUser change\n")
    keep_generated_setup_local(load_project_config(repository))
    assert "M AGENTS.md" in git(repository, "status", "--porcelain")


@pytest.mark.parametrize("redirect", ["file_symlink", "file_hardlink", "directory_symlink"])
def test_local_exclude_cannot_redirect_writes(repository: Path, tmp_path: Path, redirect: str) -> None:
    assert cli.main(["init", "--repo", str(repository)]) == 0
    external = tmp_path / "unrelated"
    external.write_bytes(b"private unrelated content\n")
    info = repository / ".git/info"
    exclude = info / "exclude"
    exclude.unlink()
    if redirect == "file_symlink":
        exclude.symlink_to(external)
    elif redirect == "file_hardlink":
        exclude.hardlink_to(external)
    else:
        info.rmdir()
        external_dir = tmp_path / "unrelated-directory"
        external_dir.mkdir()
        (external_dir / "exclude").write_bytes(external.read_bytes())
        info.symlink_to(external_dir, target_is_directory=True)
    with pytest.raises(ProjectConfigError):
        keep_generated_setup_local(load_project_config(repository))
    assert external.read_bytes() == b"private unrelated content\n"
    if redirect == "directory_symlink":
        assert (external_dir / "exclude").read_bytes() == external.read_bytes()


def test_untrusted_configuration_cannot_modify_git_metadata(repository: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert cli.main(["init", "--repo", str(repository)]) == 0
    config = load_project_config(repository)
    exclude = repository / ".git/info/exclude"
    before = exclude.read_bytes()
    monkeypatch.setenv("AI_HARNESS_CONFIG_HOME", str(tmp_path / "different-user"))
    with pytest.raises(ProjectConfigError, match="trusted"):
        keep_generated_setup_local(config)
    assert exclude.read_bytes() == before


def test_repository_ignore_override_is_preserved(repository: Path) -> None:
    (repository / ".gitignore").write_text("!/AGENTS.md\n")
    git(repository, "add", ".gitignore")
    git(repository, "commit", "-m", "keep instructions visible")
    assert cli.main(["init", "--repo", str(repository)]) == 0
    assert (repository / ".gitignore").read_text() == "!/AGENTS.md\n"
    assert git(repository, "status", "--porcelain") == "?? AGENTS.md"


def test_linked_worktree_uses_local_common_git_metadata(repository: Path, tmp_path: Path) -> None:
    checkout = tmp_path / "linked"
    git(repository, "worktree", "add", "-b", "feat/linked", str(checkout), "main")
    assert cli.main(["init", "--repo", str(checkout)]) == 0
    assert git(checkout, "status", "--porcelain") == ""
    assert b"/.agent/project.yaml\n" in (repository / ".git/info/exclude").read_bytes()


def test_branch_switch_preserves_ignored_local_file_collision(repository: Path) -> None:
    (repository / "AGENTS.md").write_text("tracked base instructions\n")
    git(repository, "add", "AGENTS.md")
    git(repository, "commit", "-m", "base instructions")
    git(repository, "switch", "-c", "feat/local")
    git(repository, "rm", "AGENTS.md")
    git(repository, "commit", "-m", "local instructions instead")
    assert cli.main(["init", "--repo", str(repository)]) == 0
    before = (repository / "AGENTS.md").read_bytes()
    result = prepare_task_branch(repository, "feat/collision", "main")
    assert result["execution_status"] == "blocked"
    assert git(repository, "branch", "--show-current") == "feat/local"
    assert (repository / "AGENTS.md").read_bytes() == before
    assert "feat/collision" not in git(repository, "branch", "--list")


def test_tracked_configuration_is_not_hidden(repository: Path) -> None:
    assert cli.main(["init", "--repo", str(repository)]) == 0
    # Explicit tracking is intentional here; it models an older shared config.
    git(repository, "add", "-f", ".agent/project.yaml")
    git(repository, "commit", "-m", "shared project identity")
    path = repository / ".agent/project.yaml"
    path.write_text(path.read_text() + "\n# Local edit\n")
    keep_generated_setup_local(load_project_config(repository))
    assert "M .agent/project.yaml" in git(repository, "status", "--porcelain")

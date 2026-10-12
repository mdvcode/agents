from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from ai_harness.context.models import KnowledgeRequest
from ai_harness.context.sources import ProjectProfileSource
from ai_harness.project_validation import effective_project_profile
from scripts import agent_role_runner, security_scan, validate_artifacts

ROOT = Path(__file__).resolve().parents[1]


def python_target(repository: Path) -> Path:
    repository.mkdir(parents=True, exist_ok=True)
    (repository / "pyproject.toml").write_text("[project]\nname = 'example'\n")
    (repository / "uv.lock").write_text("version = 1\n")
    (repository / "tests").mkdir()
    return repository


def test_python_uv_commands_replace_harness_gates_and_quote_paths(
    tmp_path: Path,
) -> None:
    repository = python_target(tmp_path / "project with spaces")
    profile = effective_project_profile(ROOT, "agent_workspace", repository)
    assert profile["validation_variant"] == "python_uv"
    assert profile["quality_commands"]["required"] == [
        "uv run --frozen ruff check .",
        "uv run --frozen ruff format --check .",
        "uv run --frozen pytest",
    ]
    args = shlex.split(profile["security_commands"]["required"][0])
    assert args[args.index("--repo") + 1] == str(repository)
    assert "--git-worktree" in args
    assert not (repository / "Makefile").exists()


@pytest.mark.parametrize("marker", [".agent-policy.yaml", "manage.py", "package.json"])
def test_excluded_project_markers_keep_the_base_gates(
    tmp_path: Path, marker: str
) -> None:
    repository = python_target(tmp_path)
    (repository / marker).write_text("")
    profile = effective_project_profile(ROOT, "agent_workspace", repository)
    assert "validation_variant" not in profile
    assert profile["quality_commands"]["required"] == ["make check"]


def test_harness_and_explicit_framework_profiles_are_unchanged(tmp_path: Path) -> None:
    repository = python_target(tmp_path)
    assert effective_project_profile(ROOT, "agent_workspace", ROOT)["quality_commands"][
        "required"
    ] == ["make check"]
    for name in ("django", "nextjs_web"):
        assert effective_project_profile(
            ROOT, name, repository
        ) == effective_project_profile(ROOT, name)


def test_target_local_identity_cannot_override_checks(tmp_path: Path) -> None:
    repository = python_target(tmp_path)
    (repository / ".agent").mkdir()
    (repository / ".agent" / "project.yaml").write_text(
        "quality_commands: {required: ['true']}\n"
    )
    assert (
        effective_project_profile(ROOT, "agent_workspace", repository)[
            "quality_commands"
        ]["required"][-1]
        == "uv run --frozen pytest"
    )


def test_checkpoint_profile_replaces_only_obsolete_gates(tmp_path: Path) -> None:
    repository = python_target(tmp_path / "project")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "project_profile.json").write_text(
        json.dumps(
            {
                "quality_commands_selected": ["make check", "git diff HEAD --check"],
                "security_commands_selected": ["make security"],
            }
        )
    )
    agent_role_runner.ensure_project_profile_artifact(
        artifacts, "agent_workspace", repository
    )
    profile = json.loads((artifacts / "project_profile.json").read_text())
    assert "make check" not in profile["quality_commands_selected"]
    assert "git diff HEAD --check" in profile["quality_commands_selected"]
    assert "make security" not in profile["security_commands_selected"]
    assert "Repository validation variant: python_uv." in profile["reasons"]


def test_deterministic_checks_execute_effective_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = python_target(tmp_path / "project")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    attempted = []

    def run(commands, _repository, _timeout):
        attempted.extend(commands)
        return [
            {"command": command, "status": "pass", "returncode": 0, "output": "ok"}
            for command in commands
        ]

    monkeypatch.setattr(agent_role_runner, "run_bounded_commands", run)
    result = agent_role_runner.run_deterministic_security(
        project_profile="agent_workspace",
        repository=repository,
        artifacts_dir=artifacts,
        timeout_seconds=180,
        required_checks={"secret_scan"},
    )
    assert result["status"] == "completed"
    assert len(attempted) == 1
    assert "--git-worktree" in attempted[0]
    assert (
        agent_role_runner.profile_required_commands(
            "agent_workspace", "quality_commands", repository
        )[-1]
        == "uv run --frozen pytest"
    )


def test_git_worktree_scan_covers_unstaged_and_new_files_excluding_venv(
    tmp_path: Path,
) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".gitignore").write_text(".venv/\n")
    (tmp_path / "tracked.py").write_text("safe\n")
    subprocess.run(["git", "add", ".gitignore", "tracked.py"], cwd=tmp_path, check=True)
    token = "ghp_" + "X" * 24
    (tmp_path / "tracked.py").write_text(token)
    (tmp_path / "new.py").write_text(token)
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "library.py").write_text(token)
    paths = security_scan.git_worktree_paths(tmp_path)
    assert set(paths) == {".gitignore", "tracked.py", "new.py"}
    findings = security_scan.scan(
        repo=tmp_path, profile="agent_workspace", staged_paths=paths
    )
    assert any("tracked.py" in finding for finding in findings)
    assert any("new.py" in finding for finding in findings)
    assert not any("library.py" in finding for finding in findings)


def test_git_worktree_scan_fails_closed_without_git(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="cannot enumerate"):
        security_scan.git_worktree_paths(tmp_path)


def test_variant_policy_rejects_missing_security_and_escaping_markers() -> None:
    import yaml

    policy = yaml.safe_load((ROOT / ".agent-project-profiles.yaml").read_text())
    variant = policy["profiles"]["agent_workspace"]["validation_variants"]["python_uv"]
    variant["security_commands"]["required"] = []
    variant["required_markers"] = ["../other"]
    errors = validate_artifacts.validate_project_profiles_data(policy)
    assert any("security_commands.required" in error for error in errors)
    assert any("repository-relative" in error for error in errors)


def test_compiled_identity_exposes_effective_gates(tmp_path: Path) -> None:
    repository = python_target(tmp_path)
    request = KnowledgeRequest(
        "Analyze",
        repository,
        "implementation-agent",
        "codex-sdk",
        "example",
        "agent_workspace",
    )
    identity = next(
        document
        for document in ProjectProfileSource(ROOT).collect(request)
        if document.path == "resolved"
    )
    profile = json.loads(identity.content)
    assert profile["validation_variant"] == "python_uv"
    assert profile["quality_commands_required"][-1] == "uv run --frozen pytest"
    assert "--git-worktree" in profile["security_commands_required"][0]


def test_documented_placeholder_does_not_hide_real_keys(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("ANTHROPIC_API_KEY=your_anthropic_api_key\n")
    assert security_scan.scan(repo=tmp_path, full_repo=True) == []
    (tmp_path / "README.md").write_text(
        "ANTHROPIC_API_KEY=" + "your_anthropic_api_key_extra\n"
    )
    assert security_scan.scan(repo=tmp_path, full_repo=True)


def test_git_worktree_cli_rejects_symlink_escape(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    outside = tmp_path / "outside.txt"
    outside.write_text("private content")
    (repository / "link.txt").symlink_to(outside)
    import sys

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/security_scan.py"),
            "--repo",
            str(repository),
            "--git-worktree",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 1
    assert "escapes repository" in result.stdout
    assert "private content" not in result.stdout


def test_same_run_refresh_retains_risk_and_scope(tmp_path: Path) -> None:
    repository = python_target(tmp_path / "project")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "project_profile.json").write_text(
        json.dumps(
            {
                "quality_commands_selected": ["make check"],
                "security_commands_selected": ["make security"],
            }
        )
    )
    (artifacts / "risk.json").write_text('{"risk_class":"medium"}')
    (artifacts / "plan.md").write_text(
        "# TASK\nOriginal goal\nThe Context Compiler prepared this plan\n# CHECKS_TO_RUN\n- make check\n\n# DONE_CRITERIA\nOriginal criteria\n"
    )
    assert agent_role_runner.refresh_resumed_validation(
        artifacts, "agent_workspace", repository, "fast"
    )
    plan = (artifacts / "plan.md").read_text()
    assert "make check" not in plan
    assert "uv run --frozen pytest" in plan
    assert "Original goal" in plan and "Original criteria" in plan
    assert (artifacts / "risk.json").read_text() == '{"risk_class":"medium"}'
    assert not agent_role_runner.refresh_resumed_validation(
        artifacts, "agent_workspace", repository, "fast"
    )

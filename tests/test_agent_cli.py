from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ai_harness import cli
from ai_harness.build import harness_build_fingerprint
from ai_harness.project import load_project_config, safe_branch
from agent_role_runner import resolve_registry_record
from repository_registry import load_local_project_record
from runtime_contracts import load_json, validate_contract
from worktree_manager import create_worktree


@pytest.fixture(autouse=True)
def isolate_local_project_trust(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_HARNESS_CONFIG_HOME", str(tmp_path / "user-config"))


def initialize_git_repository(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    (path / "README.md").write_text("project\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True, text=True)


def commit_all(path: Path, message: str = "project setup") -> None:
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", message], cwd=path, check=True, capture_output=True, text=True)


def configure_temporary_harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    state_root = tmp_path / "harness-state"
    state_root.mkdir()
    (state_root / ".agent-recovery.yaml").write_bytes(
        (ROOT / ".agent-recovery.yaml").read_bytes()
    )
    monkeypatch.setattr(cli, "harness_home", lambda: state_root)
    monkeypatch.setattr(cli, "missing_runtime_imports", lambda: [])
    monkeypatch.setattr(cli, "verify_managed_sdk_session", lambda _root: "ready")
    monkeypatch.setattr(
        cli,
        "run_worker_command",
        lambda _root, action, workers=0: {
            "status": "starting",
            "pid": 4321,
            "action": action,
            "workers": workers,
        },
    )
    return state_root


def test_agent_task_checks_managed_sdk_transport_before_git_or_queue_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    commit_all(repository)
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)

    def fail_transport(_root: Path) -> str:
        raise cli.CLIError("managed Codex SDK worker transport is unavailable: socket failed")

    monkeypatch.setattr(cli, "verify_managed_sdk_session", fail_transport)

    result = cli.main(
        ["task", "Implement safely", "--repo", str(repository), "--task-id", "safe-transport"]
    )

    assert result == 2
    assert "worker transport is unavailable" in capsys.readouterr().err
    assert subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() in {"main", "master"}
    assert not (state_root / ".agent-queue" / "tasks.db").exists()


def test_agent_failures_shows_worker_failure_before_workflow_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)
    task_queue = cli.load_harness_module(state_root, "task_queue")
    queue = task_queue.TaskQueue(state_root / ".agent-queue" / "tasks.db")
    queued = queue.enqueue(
        task_key="pre-workflow-failure",
        payload={"task_id": "pre-workflow-failure", "repository": str(repository)},
        run_id="run-pre-workflow-failure",
    )
    claimed = queue.claim(worker_id="worker-1", lease_seconds=30)
    assert claimed is not None and claimed.id == queued.id
    assert queue.mark_running(queued.id, "worker-1")
    queue.move_to_dead_letter(
        task_id=queued.id,
        worker_id="worker-1",
        run_id="run-pre-workflow-failure",
        error="managed session failed",
        failure_kind="internal_error",
        failure_id="failure-before-workflow",
    )
    failures_dir = (
        state_root
        / ".agent-runs"
        / "run-pre-workflow-failure"
        / "failures"
    )
    failures_dir.mkdir(parents=True)
    (failures_dir / "failure-before-workflow.json").write_text(
        json.dumps(
            {
                "failure_id": "failure-before-workflow",
                "run_id": "run-pre-workflow-failure",
                "kind": "internal_error",
                "error_type": "SdkSessionUnavailable",
                "attempt": 1,
                "max_attempts": 1,
                "role": "worker",
                "message": "managed session failed",
                "retryable": False,
                "checkpoint": "before_worker_execute",
            }
        ),
        encoding="utf-8",
    )

    assert cli.main(
        [
            "failures",
            "--repo",
            str(repository),
            "--run-id",
            "run-pre-workflow-failure",
            "--json",
        ]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["count"] == 1
    assert result["items"][0]["error_type"] == "SdkSessionUnavailable"


def test_agent_retry_requeues_failure_that_happened_before_workflow_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)
    task_queue = cli.load_harness_module(state_root, "task_queue")
    queue = task_queue.TaskQueue(state_root / ".agent-queue" / "tasks.db")
    queued = queue.enqueue(
        task_key="retry-pre-workflow",
        payload={"task_id": "retry-pre-workflow", "repository": str(repository)},
        run_id="run-retry-pre-workflow",
    )
    claimed = queue.claim(worker_id="worker-1", lease_seconds=30)
    assert claimed is not None and claimed.id == queued.id
    assert queue.mark_running(queued.id, "worker-1")
    queue.move_to_dead_letter(
        task_id=queued.id,
        worker_id="worker-1",
        run_id="run-retry-pre-workflow",
        error="managed session failed",
        failure_kind="internal_error",
        failure_id="failure-before-workflow",
    )

    assert cli.main(
        ["retry", "run-retry-pre-workflow", "--repo", str(repository), "--json"]
    ) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "retry_wait"
    assert queue.get(queued.id).status == "retry_wait"
    assert not (
        state_root / ".agent-runs" / "run-retry-pre-workflow" / "workflow.json"
    ).exists()


def test_agent_failures_hides_recovered_history_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)
    task_queue = cli.load_harness_module(state_root, "task_queue")
    queue = task_queue.TaskQueue(state_root / ".agent-queue" / "tasks.db")
    queued = queue.enqueue(
        task_key="recovered-history",
        payload={"task_id": "recovered-history", "repository": str(repository)},
        run_id="run-recovered-history",
    )
    claimed = queue.claim(worker_id="worker-1", lease_seconds=30)
    assert claimed is not None and claimed.id == queued.id
    assert queue.mark_running(queued.id, "worker-1")
    queue.finish(
        task_id=queued.id,
        worker_id="worker-1",
        status="completed",
        run_id="run-recovered-history",
    )
    failures_dir = state_root / ".agent-runs" / "run-recovered-history" / "failures"
    failures_dir.mkdir(parents=True)
    (failures_dir / "failure-recovered.json").write_text(
        json.dumps(
            {
                "failure_id": "failure-recovered",
                "run_id": "run-recovered-history",
                "kind": "invalid_output",
                "error_type": "InvalidArtifactOutput",
                "attempt": 1,
                "max_attempts": 2,
                "role": "reviewer",
                "message": "repaired",
                "retryable": True,
                "checkpoint": "before_reviewer",
            }
        ),
        encoding="utf-8",
    )

    assert cli.main(
        ["failures", "--repo", str(repository), "--run-id", "run-recovered-history", "--json"]
    ) == 0
    current = json.loads(capsys.readouterr().out)
    assert current["count"] == 0
    assert current["resolved_count"] == 1

    assert cli.main(
        [
            "failures",
            "--repo",
            str(repository),
            "--run-id",
            "run-recovered-history",
            "--include-resolved",
            "--json",
        ]
    ) == 0
    history = json.loads(capsys.readouterr().out)
    assert history["count"] == 1
    assert history["items"][0]["resolved"] is True


def test_agent_init_creates_local_config_and_preserves_existing_agents_file(
    tmp_path: Path,
    capsys: object,
) -> None:
    repository = tmp_path / "my-project"
    repository.mkdir()
    (repository / "package.json").write_text("{}\n", encoding="utf-8")
    existing_agents = "# Existing project rules\n"
    (repository / "AGENTS.md").write_text(existing_agents, encoding="utf-8")

    assert cli.main(["init", "--repo", str(repository), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    config = load_project_config(repository)

    assert result["status"] == "initialized"
    assert result["created"] == {"project_config": True, "agents_md": False}
    assert config.project_id == "my-project"
    assert config.profile == "nextjs_web"
    assert config.runtime_provider == "codex-sdk"
    assert Path(result["local_trust"]).is_file()
    assert (repository / "AGENTS.md").read_text(encoding="utf-8") == existing_agents

    assert cli.main(["init", "--repo", str(repository), "--json"]) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["created"] == {"project_config": False, "agents_md": False}


def test_agent_init_accepts_gitignored_local_setup_without_force_add_guidance(
    tmp_path: Path,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    (repository / ".gitignore").write_text(".agent/\nAGENTS.md\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore"], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-m", "ignore local agent setup"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )

    assert cli.main(["init", "--repo", str(repository), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["git"]["ignored_setup_files"] == [".agent/project.yaml", "AGENTS.md"]
    assert cli.main(["init", "--repo", str(repository)]) == 0
    output = capsys.readouterr().out
    assert "ignored by Git" in output
    assert "no git add -f is needed" in output


def test_agent_dashboard_opens_authenticated_local_control_center(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    opened: list[str] = []
    served: list[dict[str, object]] = []

    class ControlPlane:
        @staticmethod
        def serve_control_plane(**kwargs: object) -> None:
            served.append(kwargs)
            kwargs["on_ready"](9876)  # type: ignore[index,operator]

    monkeypatch.setattr(cli, "load_harness_module", lambda _root, _name: ControlPlane)
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: opened.append(url) or True)

    assert cli.main(["dashboard", "--repo", str(repository), "--port", "9876"]) == 0

    assert served[0]["default_repository"] == repository
    assert opened[0].startswith("http://127.0.0.1:9876/dashboard#token=")
    assert "repo=" in opened[0]
    output = capsys.readouterr().out
    assert "Dashboard ready: http://127.0.0.1:9876/dashboard" in output
    assert "token=" not in output


def test_agent_init_retrusts_existing_nondefault_config_without_replacing_it(
    tmp_path: Path,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    assert cli.main(
        [
            "init",
            "--repo",
            str(repository),
            "--profile",
            "django",
            "--base-branch",
            "develop",
            "--branch-prefix",
            "fix/",
        ]
    ) == 0
    capsys.readouterr()

    assert cli.main(["init", "--repo", str(repository), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    config = load_project_config(repository)

    assert result["created"] == {"project_config": False, "agents_md": False}
    assert config.profile == "django"
    assert config.base_branch == "develop"
    assert config.branch_prefix == "fix/"


def test_agent_init_force_updates_only_explicit_config_and_preserves_agents(
    tmp_path: Path,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    existing_agents = "# Keep these project rules\n"
    (repository / "AGENTS.md").write_text(existing_agents, encoding="utf-8")
    assert cli.main(
        [
            "init",
            "--repo",
            str(repository),
            "--profile",
            "django",
            "--base-branch",
            "develop",
            "--branch-prefix",
            "fix/",
        ]
    ) == 0
    capsys.readouterr()

    assert cli.main(
        ["init", "--repo", str(repository), "--force", "--base-branch", "release"]
    ) == 0
    config = load_project_config(repository)

    assert config.profile == "django"
    assert config.base_branch == "release"
    assert config.branch_prefix == "fix/"
    assert (repository / "AGENTS.md").read_text(encoding="utf-8") == existing_agents


def test_agent_task_is_high_level_idempotent_queue_intake(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    commit_all(repository)
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)

    command = [
        "task",
        "Fix login",
        "--repo",
        str(repository),
        "--task-id",
        "fix-login",
        "--json",
    ]
    assert cli.main(command) == 0
    first = json.loads(capsys.readouterr().out)
    assert cli.main(command) == 0
    second = json.loads(capsys.readouterr().out)

    assert first["queue_task_id"] == second["queue_task_id"]
    assert first["task_id"] == "fix-login"
    assert first["branch"] == "feat/fix-login"
    assert first["workspace_mode"] == "checkout"
    assert first["worker"] == {"status": "starting", "pid": 4321}
    assert subprocess.run(
        ["git", "branch", "--show-current"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip() == "feat/fix-login"
    assert not (state_root / ".agent-worktrees").exists()
    db_path = state_root / ".agent-queue" / "tasks.db"
    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    assert count == 1
    assert len(list((state_root / ".agent-queue" / "events").glob("*.json"))) == 1


def test_agent_task_accepts_custom_prefix_and_auto_starts_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(
        ["init", "--repo", str(repository), "--branch-prefix", "release/2026/"]
    ) == 0
    commit_all(repository)
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)
    worker_calls: list[tuple[str, int]] = []

    def start_worker(_root: Path, action: str, *, workers: int = 0) -> dict[str, object]:
        worker_calls.append((action, workers))
        return {"status": "starting", "pid": 9876}

    monkeypatch.setattr(cli, "run_worker_command", start_worker)

    assert cli.main(
        ["task", "Ship release notes", "--repo", str(repository), "--task-id", "release-notes", "--json"]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["branch"] == "release/2026/release-notes"
    assert result["workspace_mode"] == "checkout"
    assert result["worker"] == {"status": "starting", "pid": 9876}
    assert worker_calls == [("start", 3)]
    assert subprocess.run(
        ["git", "branch", "--show-current"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip() == "release/2026/release-notes"
    assert not (state_root / ".agent-worktrees").exists()


@pytest.mark.parametrize(
    ("mode", "goal", "task_id"),
    [
        ("adaptive", "Compile the minimum safe execution plan", "adaptive-plan"),
        ("fast", "Fix CSS color", "fast-css"),
        ("goal", "Complete checkpointed objective", "long-goal"),
    ],
)
def test_agent_task_accepts_requested_execution_mode(
    mode: str,
    goal: str,
    task_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    commit_all(repository)
    capsys.readouterr()
    configure_temporary_harness(monkeypatch, tmp_path)

    assert cli.main(
        ["task", goal, "--repo", str(repository), "--task-id", task_id, "--mode", mode, "--json"]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["mode"] == mode


@pytest.mark.parametrize("prefix", ["../", "bad prefix/", "feature//", ".hidden/"])
def test_agent_init_rejects_unsafe_branch_prefix(
    prefix: str,
    tmp_path: Path,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()

    result = cli.main(["init", "--repo", str(repository), "--branch-prefix", prefix])

    assert result == 2
    assert "branch_prefix" in capsys.readouterr().err
    assert not (repository / ".agent" / "project.yaml").exists()


def test_agent_task_worktree_is_explicit_opt_in_and_keeps_checkout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    original_branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)

    assert cli.main(
        ["task", "Parallel work", "--repo", str(repository), "--task-id", "parallel", "--worktree", "--json"]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["workspace_mode"] == "worktree"
    assert subprocess.run(
        ["git", "branch", "--show-current"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip() == original_branch
    assert not (state_root / ".agent-worktrees").exists()


def test_agent_task_dry_run_does_not_switch_branch_start_worker_or_create_queue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository), "--branch-prefix", "chore/"]) == 0
    commit_all(repository)
    original_branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip()
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)
    worker_calls: list[str] = []
    monkeypatch.setattr(
        cli,
        "run_worker_command",
        lambda _root, action, workers=0: worker_calls.append(action) or {"status": "starting"},
    )

    assert cli.main(
        ["task", "Preview", "--repo", str(repository), "--task-id", "preview", "--dry-run", "--json"]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "dry_run"
    assert result["envelope"]["branch"] == "chore/preview"
    assert subprocess.run(
        ["git", "branch", "--show-current"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip() == original_branch
    assert worker_calls == []
    assert not (state_root / ".agent-queue").exists()


def test_agent_task_long_security_goal_always_generates_safe_bounded_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository), "--branch-prefix", "team/security/"]) == 0
    commit_all(repository)
    capsys.readouterr()
    configure_temporary_harness(monkeypatch, tmp_path)
    goal = " ".join(
        [
            "KC-432 restrict Tool source paths and URLs",
            "/bin/sh Python interpreter arbitrary commands malformed URLs",
            "transport=stdio env config loader-level rejection",
        ]
        * 80
    )

    assert cli.main(["task", goal, "--repo", str(repository), "--dry-run", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    branch = result["envelope"]["branch"]

    assert branch.startswith("team/security/")
    assert len(branch) < 255
    assert safe_branch(branch)


@pytest.mark.parametrize(
    "branch",
    ["KC-432+tool-validation", "KC-432=security", "задача/KC-432", "team/KC-432&security"],
)
def test_agent_task_current_branch_accepts_names_git_accepts(
    branch: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    subprocess.run(["git", "switch", "-c", branch], cwd=repository, check=True, capture_output=True)
    commit_all(repository)
    capsys.readouterr()
    configure_temporary_harness(monkeypatch, tmp_path)

    assert cli.main(
        ["task", "Implement KC-432", "--repo", str(repository), "--current-branch", "--dry-run", "--json"]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["envelope"]["branch"] == branch


@pytest.mark.parametrize(
    "branch",
    ["../escape", "bad branch", "bad@{branch", "feature//nested", "topic.lock"],
)
def test_agent_task_rejects_unsafe_explicit_branch_with_actionable_remediation(
    branch: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    commit_all(repository)
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)

    result = cli.main(
        ["task", "Unsafe branch", "--repo", str(repository), "--branch", branch]
    )

    assert result == 2
    error = capsys.readouterr().err
    assert repr(branch) in error
    assert "remove --branch" in error
    assert not (state_root / ".agent-queue").exists()


def test_agent_task_blocks_second_current_checkout_task_until_first_finishes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    commit_all(repository)
    capsys.readouterr()
    configure_temporary_harness(monkeypatch, tmp_path)
    assert cli.main(
        ["task", "First", "--repo", str(repository), "--task-id", "first"]
    ) == 0
    capsys.readouterr()

    result = cli.main(["task", "Second", "--repo", str(repository), "--task-id", "second"])

    assert result == 2
    assert "still owns this checkout" in capsys.readouterr().err
    assert subprocess.run(
        ["git", "branch", "--show-current"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip() == "feat/first"


def test_agent_task_replaces_paused_checkout_owner_and_preserves_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    commit_all(repository)
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)
    assert cli.main(
        ["task", "Old task", "--repo", str(repository), "--task-id", "old-task"]
    ) == 0
    capsys.readouterr()
    task_queue = cli.load_harness_module(state_root, "task_queue")
    queue = task_queue.TaskQueue(state_root / ".agent-queue" / "tasks.db")
    old = queue.claim(worker_id="worker-old")
    assert old is not None
    assert queue.mark_running(old.id, "worker-old")
    queue.finish(
        task_id=old.id,
        worker_id="worker-old",
        status="awaiting_approval",
        run_id="run-old",
        requires_human=True,
        exception_reason="Choose a timeout policy",
    )
    run_dir = state_root / ".agent-runs" / "run-old"
    run_dir.mkdir(parents=True)
    (run_dir / "workflow.json").write_text(
        json.dumps({"run_id": "run-old", "execution_status": "awaiting_approval"}) + "\n",
        encoding="utf-8",
    )

    assert cli.main(
        [
            "task",
            "New KC-432 task",
            "--repo",
            str(repository),
            "--task-id",
            "kc-432",
            "--keep-paused",
        ]
    ) == 2
    assert "still owns this checkout" in capsys.readouterr().err

    assert cli.main(
        ["task", "New KC-432 task", "--repo", str(repository), "--task-id", "kc-432", "--json"]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["task_id"] == "kc-432"
    assert result["branch"] == "feat/kc-432"
    assert any("was replaced by this new task" in warning for warning in result["warnings"])
    assert queue.list()[0].status == "cancelled"
    workflow = json.loads((run_dir / "workflow.json").read_text(encoding="utf-8"))
    assert workflow["execution_status"] == "cancelled"
    assert workflow["superseded_by_task_id"] == "kc-432"
    assert subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == "feat/kc-432"


def test_agent_task_checks_runtime_before_superseding_paused_checkout_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    commit_all(repository)
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)
    assert cli.main(
        ["task", "Old task", "--repo", str(repository), "--task-id", "old-task"]
    ) == 0
    capsys.readouterr()
    task_queue = cli.load_harness_module(state_root, "task_queue")
    queue = task_queue.TaskQueue(state_root / ".agent-queue" / "tasks.db")
    old = queue.claim(worker_id="worker-old")
    assert old is not None
    assert queue.mark_running(old.id, "worker-old")
    queue.finish(
        task_id=old.id,
        worker_id="worker-old",
        status="awaiting_approval",
        run_id="run-old",
        requires_human=True,
        exception_reason="Choose a timeout policy",
    )
    monkeypatch.setattr(cli, "missing_runtime_imports", lambda: ["openai_codex"])

    result = cli.main(
        ["task", "New task", "--repo", str(repository), "--task-id", "new-task"]
    )

    assert result == 2
    assert "missing runtime dependencies: openai_codex" in capsys.readouterr().err
    records = queue.list()
    assert len(records) == 1
    assert records[0].status == "awaiting_approval"
    assert records[0].payload["task_id"] == "old-task"
    assert subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == "feat/old-task"


def test_agent_task_rejects_source_install_drift_before_queue_or_branch_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    original_branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert cli.main(["init", "--repo", str(repository)]) == 0
    commit_all(repository)
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)
    source = tmp_path / "harness-source"
    monkeypatch.setattr(cli, "source_build_comparison", lambda _repository, _root: (source, False))

    result = cli.main(
        ["task", "Do not queue", "--repo", str(repository), "--task-id", "drifted"]
    )

    assert result == 2
    error = capsys.readouterr().err
    assert f"agent update --source {source}" in error
    assert not (state_root / ".agent-queue" / "tasks.db").exists()
    assert subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip() == original_branch


def test_source_build_comparison_uses_pipx_local_source_for_other_projects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = tmp_path / "venv"
    installed = prefix / "share" / "ai-harness"
    source = tmp_path / "harness-source"
    repository = tmp_path / "target-project"
    for root, value in ((installed, "installed"), (source, "source")):
        (root / "ai_harness").mkdir(parents=True)
        (root / "scripts").mkdir()
        (root / "ai_harness" / "build.py").write_text(f"VALUE = {value!r}\n", encoding="utf-8")
        (root / "scripts" / "worker_service.py").write_text("RUN = True\n", encoding="utf-8")
    repository.mkdir()
    monkeypatch.setattr(cli.sys, "prefix", str(prefix))
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/fake/pipx")
    monkeypatch.setattr(cli, "pipx_installed_source", lambda _pipx: str(source))

    detected, matches = cli.source_build_comparison(repository, installed)

    assert detected == source.resolve()
    assert matches is False


def test_agent_task_preserves_queued_run_when_worker_startup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    commit_all(repository)
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)

    def fail_worker(_root: Path, _action: str, *, workers: int = 0) -> dict[str, object]:
        raise cli.CLIError("worker failed during startup: missing policy")

    monkeypatch.setattr(cli, "run_worker_command", fail_worker)

    result = cli.main(
        ["task", "Queue safely", "--repo", str(repository), "--task-id", "safe-queue"]
    )

    assert result == 2
    error = capsys.readouterr().err
    assert "was queued as run" in error
    assert "agent worker restart" in error
    task_queue = cli.load_harness_module(state_root, "task_queue")
    records = task_queue.TaskQueue(state_root / ".agent-queue" / "tasks.db").list()
    assert len(records) == 1
    assert records[0].status == "queued"
    assert records[0].payload["task_id"] == "safe-queue"


def test_ensure_worker_service_restarts_stale_live_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli,
        "worker_service_status",
        lambda _root: {
            "alive": True,
            "status": "healthy",
            "build_fingerprint": "old",
            "stale_build": True,
        },
    )
    calls: list[tuple[str, int]] = []

    def restart(_root: Path, action: str, *, workers: int = 0) -> dict[str, object]:
        calls.append((action, workers))
        return {"alive": True, "status": "healthy", "build_fingerprint": "new"}

    monkeypatch.setattr(cli, "run_worker_command", restart)

    result = cli.ensure_worker_service(tmp_path, workers=2)

    assert result["status"] == "healthy"
    assert calls == [("restart", 2)]


def test_wait_for_worker_ready_observes_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = iter(
        [
            {
                "configured": True,
                "alive": True,
                "status": "starting",
                "build_fingerprint": "",
                "stale_build": False,
                "last_error": {},
            },
            {
                "configured": True,
                "alive": True,
                "status": "healthy",
                "build_fingerprint": "current",
                "stale_build": False,
                "last_error": {},
            },
        ]
    )
    monkeypatch.setattr(cli, "worker_service_status", lambda _root: next(states))
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    result = cli.wait_for_worker_ready(tmp_path, timeout_seconds=1)

    assert result["status"] == "healthy"
    assert result["build_fingerprint"] == "current"


def test_wait_for_worker_stopped_observes_process_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = iter(
        [
            {"alive": True, "status": "stopping"},
            {"alive": False, "status": "stopped"},
        ]
    )
    monkeypatch.setattr(cli, "worker_service_status", lambda _root: next(states))
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)

    result = cli.wait_for_worker_stopped(tmp_path, timeout_seconds=1)

    assert result["status"] == "stopped"


def test_last_worker_error_recovers_raw_startup_traceback(tmp_path: Path) -> None:
    queue = tmp_path / ".agent-queue"
    queue.mkdir()
    (queue / "worker-service.log").write_text(
        "Traceback (most recent call last):\n"
        "  File \"worker_service.py\", line 1, in <module>\n"
        "FileNotFoundError: missing recovery policy\n",
        encoding="utf-8",
    )

    error = cli.last_worker_error(tmp_path)

    assert error["error_type"] == "WorkerStartupError"
    assert "missing recovery policy" in error["message"]


def test_task_worktree_can_use_a_committed_local_base_without_origin(tmp_path: Path) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    base_branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    result = create_worktree(
        repository,
        "local-task",
        "fix/local-task",
        base_branch,
        "local-run",
        runs_dir=tmp_path / "runs",
        worktrees_dir=tmp_path / "worktrees",
    )

    assert result["execution_status"] == "completed"
    assert any("using local branch" in warning for warning in result["warnings"])


def test_agent_status_is_project_scoped_and_read_only(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    other = tmp_path / "other"
    repository.mkdir()
    other.mkdir()
    assert cli.main(["init", "--repo", str(repository)]) == 0
    assert cli.main(["init", "--repo", str(other)]) == 0
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)
    assert cli.main(["task", "Mine", "--repo", str(repository), "--task-id", "mine", "--worktree"]) == 0
    assert cli.main(["task", "Other", "--repo", str(other), "--task-id", "other", "--worktree"]) == 0
    capsys.readouterr()

    assert cli.main(["status", "--repo", str(repository), "--json"]) == 0
    status = json.loads(capsys.readouterr().out)

    assert status["project"]["repository"] == str(repository.resolve())
    assert [item["task_id"] for item in status["queue"]["items"]] == ["mine"]
    assert status["queue"]["counts"] == {"queued": 1}
    assert status["worker_service"] == {
        "configured": False,
        "alive": False,
        "pid": 0,
        "service_id": "",
        "status": "not_started",
        "log": str(state_root / ".agent-queue" / "worker-service.log"),
        "last_error": {},
        "build_fingerprint": "",
        "current_build_fingerprint": harness_build_fingerprint(state_root),
        "stale_build": False,
    }


def test_attention_items_collapse_queue_history_by_run() -> None:
    tasks = [
        {
            "queue_task_id": 3,
            "task_id": "kc-363",
            "run_id": "queue-task-48",
            "status": "awaiting_approval",
            "requires_human": True,
            "exception_reason": "latest approval",
        },
        {
            "queue_task_id": 2,
            "task_id": "kc-363",
            "run_id": "queue-task-48",
            "status": "awaiting_approval",
            "requires_human": True,
            "exception_reason": "older approval",
        },
    ]
    runs = [
        {
            "run_id": "queue-task-48",
            "current_role": "implementation-agent",
            "approval": {"status": "pending"},
            "attention": {
                "required": True,
                "summary": "Approval required",
                "details": [],
                "action": "approve",
            },
        }
    ]

    items = cli.attention_items(tasks, runs)

    assert len(items) == 1
    assert items[0]["queue_task_id"] == 3
    assert items[0]["summary"] == "latest approval"


@pytest.mark.parametrize(
    "status",
    [
        "running",
        "resuming",
        "retry_wait",
        "waiting_children",
        "planned",
        "completed",
        "cancelled",
    ],
)
def test_workflow_attention_suppresses_stale_raw_attention(status: str) -> None:
    attention = cli.workflow_attention(
        {
            "execution_status": status,
            "attention": {
                "required": True,
                "summary": "An earlier question was already answered.",
            },
        }
    )

    assert attention["required"] is False


@pytest.mark.parametrize("status", ["awaiting_approval", "blocked"])
def test_workflow_attention_preserves_current_raw_attention(status: str) -> None:
    attention = cli.workflow_attention(
        {
            "execution_status": status,
            "attention": {
                "required": True,
                "summary": "Current action is required.",
            },
        }
    )

    assert attention["required"] is True
    assert attention["summary"] == "Current action is required."


def test_running_project_run_with_stale_attention_is_not_actionable(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "active-run"
    run_dir.mkdir(parents=True)
    (run_dir / "workflow.json").write_text(
        json.dumps(
            {
                "task_id": "active-task",
                "repository": str(tmp_path),
                "execution_status": "running",
                "attention": {
                    "required": True,
                    "summary": "An earlier question was already answered.",
                },
            }
        ),
        encoding="utf-8",
    )

    runs = cli.project_runs(run_dir.parent, tmp_path)

    assert runs[0]["attention"]["required"] is False
    assert cli.attention_items([], runs) == []


def test_agent_approve_resumes_the_only_pending_project_run(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)
    approval_lifecycle = cli.load_harness_module(state_root, "approval_lifecycle")
    run_dir = state_root / ".agent-runs" / "queue-task-4"
    (run_dir / "artifacts").mkdir(parents=True)
    workflow = {
        "run_id": "queue-task-4",
        "task_id": "photo-cards",
        "goal": "Add photo cards",
        "project": "nextjs_web",
        "repository": str(repository),
        "branch": "tast/photo-cards",
        "base_branch": "main",
        "execution_status": "awaiting_approval",
        "last_route": {"next_role": "quality-runner", "reason": "Approval required."},
        "roles": [
            {
                "role": "quality-runner",
                "result": {"status": "completed", "blockers": []},
            }
        ],
    }
    (run_dir / "workflow.json").write_text(json.dumps(workflow), encoding="utf-8")
    approval_lifecycle.request_approval(run_dir, reason="Approval required.")

    assert cli.main(["approve", "--repo", str(repository), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "queued"
    assert result["run_id"] == "queue-task-4"
    assert result["checkpoint_role"] == "quality-runner"
    approval = json.loads((run_dir / "artifacts" / "approval.json").read_text(encoding="utf-8"))
    resumed = json.loads((run_dir / "workflow.json").read_text(encoding="utf-8"))
    assert approval["status"] == "consumed"
    assert resumed["execution_status"] == "resuming"
    assert resumed["resume_role"] == "quality-runner"


def test_agent_answer_records_requested_input_and_resumes_same_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)
    approval_lifecycle = cli.load_harness_module(state_root, "approval_lifecycle")
    run_dir = state_root / ".agent-runs" / "run-question"
    (run_dir / "artifacts").mkdir(parents=True)
    workflow = {
        "run_id": "run-question",
        "task_id": "choose-format",
        "goal": "Choose export format",
        "project": "agent_workspace",
        "repository": str(repository),
        "branch": "feat/choose-format",
        "base_branch": "main",
        "execution_status": "awaiting_approval",
        "current_role": "planner",
        "resume_role": "planner",
        "attention": {
            "required": True,
            "summary": "Which export format should be used?",
            "details": ["Choose CSV or JSON."],
            "role": "planner",
            "action": "answer_or_approve",
            "fingerprint": "sha256:question",
            "requirement": {
                "requirement_id": "export_format",
                "semantic_aliases": ["export format"],
                "source_question_id": "export_format",
            },
            "question": {
                "id": "export_format",
                "options": [
                    {
                        "label": "JSON",
                        "description": "Preserves nested data.",
                        "value": "Use JSON",
                        "recommended": True,
                    },
                    {
                        "label": "CSV",
                        "description": "Simple tabular export.",
                        "value": "Use CSV",
                        "recommended": False,
                    },
                ],
                "allow_custom": True,
            },
        },
        "last_route": {"next_role": "approval-gate", "reason": "User input is required."},
        "roles": [
            {
                "role": "planner",
                "result": {
                    "status": "awaiting_approval",
                    "summary": "Which export format should be used?",
                    "blockers": ["Choose CSV or JSON."],
                },
            }
        ],
    }
    (run_dir / "workflow.json").write_text(json.dumps(workflow), encoding="utf-8")
    checkpoints = run_dir / "checkpoints"
    checkpoints.mkdir()
    (checkpoints / "planner.json").write_text(
        json.dumps(
            {
                "run_id": "run-question",
                "role": "planner",
                "state": "role_validating",
                "attempt": 1,
                "worktree": str(repository),
                "input_fingerprint": "input",
                "output_fingerprint": "output",
                "artifacts": ["plan.md"],
                "side_effects": [],
            }
        ),
        encoding="utf-8",
    )
    approval_lifecycle.request_approval(run_dir, reason="Which export format should be used?")

    assert cli.main(
        ["answer", "run-question", "Use JSON", "--repo", str(repository), "--json"]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "queued"
    assert result["run_id"] == "run-question"
    assert result["answer_recorded"] is True
    human_input = json.loads((run_dir / "human-input.json").read_text(encoding="utf-8"))
    assert human_input["entries"][-1]["response"] == "Use JSON"
    assert human_input["entries"][-1]["question_id"] == "export_format"
    assert human_input["entries"][-1]["question_fingerprint"] == "sha256:question"
    assert human_input["entries"][-1]["requirement_id"] == "export_format"
    assert (run_dir / "human-input.json").stat().st_mode & 0o777 == 0o600
    approval = json.loads((run_dir / "artifacts" / "approval.json").read_text(encoding="utf-8"))
    resumed = json.loads((run_dir / "workflow.json").read_text(encoding="utf-8"))
    assert approval["status"] == "consumed"
    assert resumed["execution_status"] == "resuming"
    assert resumed["resume_role"] == "planner"
    assert "attention" not in resumed
    assert resumed["blockers"] == []
    assert resumed["attention_history"][-1]["resolution"] == "answer_recorded"
    assert resumed["closed_requirements"][-1]["requirement_id"] == "export_format"
    assert resumed["closed_requirements"][-1]["resolution"] == "answer_recorded"
    checkpoint = json.loads((checkpoints / "planner.json").read_text(encoding="utf-8"))
    assert checkpoint["state"] == "role_pending"
    assert checkpoint["output_fingerprint"] == ""
    assert checkpoint["artifacts"] == []


def test_agent_answer_rejects_a_choice_that_requires_missing_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)
    approval_lifecycle = cli.load_harness_module(state_root, "approval_lifecycle")
    run_dir = state_root / ".agent-runs" / "run-required-details"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "workflow.json").write_text(
        json.dumps(
            {
                "run_id": "run-required-details",
                "task_id": "provide-contract",
                "repository": str(repository),
                "execution_status": "awaiting_approval",
                "current_role": "implementation-agent",
                "resume_role": "implementation-agent",
                "attention": {
                    "required": True,
                    "summary": "Provide the backend contract.",
                    "details": ["Paste the contract text."],
                    "role": "implementation-agent",
                    "action": "answer",
                    "question": {
                        "id": "backend_contract",
                        "options": [
                            {
                                "label": "Paste contract",
                                "description": "Provide the complete wire contract.",
                                "value": "paste_contract",
                                "recommended": True,
                                "requires_input": True,
                            },
                            {
                                "label": "Cancel",
                                "description": "Do not continue.",
                                "value": "cancel",
                                "recommended": False,
                                "requires_input": False,
                            },
                        ],
                        "allow_custom": True,
                    },
                },
                "roles": [
                    {
                        "role": "implementation-agent",
                        "result": {
                            "status": "awaiting_approval",
                            "summary": "Provide the backend contract.",
                            "blockers": ["Paste the contract text."],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    checkpoints = run_dir / "checkpoints"
    checkpoints.mkdir()
    (checkpoints / "implementation-agent.json").write_text(
        json.dumps(
            {
                "run_id": "run-required-details",
                "role": "implementation-agent",
                "state": "role_validating",
                "attempt": 1,
                "worktree": str(repository),
                "input_fingerprint": "input",
                "output_fingerprint": "output",
                "artifacts": [],
                "side_effects": [],
            }
        ),
        encoding="utf-8",
    )
    approval_lifecycle.request_approval(run_dir, reason="Provide the backend contract.")

    assert cli.main(
        [
            "answer",
            "run-required-details",
            "paste_contract",
            "--repo",
            str(repository),
        ]
    ) == 2

    assert "requires accompanying details" in capsys.readouterr().err
    assert not (run_dir / "human-input.json").exists()


def test_agent_answer_cannot_replace_explicit_risk_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)
    approval_lifecycle = cli.load_harness_module(state_root, "approval_lifecycle")
    run_dir = state_root / ".agent-runs" / "run-risk"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "workflow.json").write_text(
        json.dumps(
            {
                "run_id": "run-risk",
                "task_id": "risk-task",
                "repository": str(repository),
                "execution_status": "awaiting_approval",
                "current_role": "risk-classifier",
                "attention": {
                    "required": True,
                    "summary": "HIGH risk requires approval.",
                    "details": [],
                    "role": "risk-classifier",
                    "action": "approve",
                },
                "last_route": {"next_role": "approval-gate", "reason": "HIGH risk requires approval."},
                    "roles": [{"role": "risk-classifier", "result": {"status": "completed"}}],
            }
        ),
        encoding="utf-8",
    )
    approval_lifecycle.request_approval(run_dir, reason="HIGH risk requires approval.")

    result = cli.main(["answer", "run-risk", "continue", "--repo", str(repository)])

    assert result == 2
    assert "explicit approval decision" in capsys.readouterr().err
    assert not (run_dir / "human-input.json").exists()


def test_agent_status_and_watch_show_actionable_attention(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)
    task_queue = cli.load_harness_module(state_root, "task_queue")
    approval_lifecycle = cli.load_harness_module(state_root, "approval_lifecycle")
    queue = task_queue.TaskQueue(state_root / ".agent-queue" / "tasks.db")
    queued = queue.enqueue(
        task_key="question-task",
        payload={"task_id": "question-task", "repository": str(repository)},
    )
    claimed = queue.claim(worker_id="worker-1", lease_seconds=30)
    assert claimed is not None and claimed.id == queued.id
    assert queue.mark_running(queued.id, "worker-1")
    queue.finish(
        task_id=queued.id,
        worker_id="worker-1",
        status="awaiting_approval",
        run_id="run-question-status",
        requires_human=True,
        exception_reason="Which API environment should be used?",
    )
    run_dir = state_root / ".agent-runs" / "run-question-status"
    (run_dir / "artifacts").mkdir(parents=True)
    (run_dir / "workflow.json").write_text(
        json.dumps(
            {
                "run_id": "run-question-status",
                "task_id": "question-task",
                "repository": str(repository),
                "execution_status": "awaiting_approval",
                "current_role": "implementation-agent",
                "attention": {
                    "required": True,
                    "summary": "Which API environment should be used?",
                    "details": ["Choose staging or local."],
                    "role": "implementation-agent",
                    "action": "answer",
                    "question": {
                        "id": "api_environment",
                        "options": [
                            {
                                "label": "Local",
                                "description": "Use local services.",
                                "value": "local",
                                "recommended": True,
                            },
                            {
                                "label": "Staging",
                                "description": "Use shared staging services.",
                                "value": "staging",
                                "recommended": False,
                            },
                        ],
                        "allow_custom": True,
                    },
                },
                "last_route": {"next_role": "approval-gate", "reason": "Input required."},
                    "roles": [
                        {
                            "role": "implementation-agent",
                            "result": {
                                "status": "awaiting_approval",
                                "summary": "Which API environment should be used?",
                                "blockers": ["Choose staging or local."],
                            },
                        }
                    ],
            }
        ),
        encoding="utf-8",
    )
    approval_lifecycle.request_approval(run_dir, reason="Which API environment should be used?")

    assert cli.main(["status", "--repo", str(repository)]) == 0
    status_output = capsys.readouterr().out
    assert "ATTENTION REQUIRED: question-task" in status_output
    assert "Which API environment should be used?" in status_output
    assert "option 1: Local (recommended)" in status_output
    assert "option 2: Staging" in status_output
    assert "agent answer" in status_output
    assert "agent approve" not in status_output

    assert cli.main(
        ["watch", "--repo", str(repository), "--task-id", "question-task", "--timeout", "1"]
    ) == 0
    watch_output = capsys.readouterr().out
    assert "ATTENTION REQUIRED: question-task" in watch_output
    assert "agent answer" in watch_output


def test_agent_watch_returns_immediately_when_worker_is_not_running(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)
    task_queue = cli.load_harness_module(state_root, "task_queue")
    task_queue.TaskQueue(state_root / ".agent-queue" / "tasks.db").enqueue(
        task_key="waiting-task",
        payload={"task_id": "waiting-task", "repository": str(repository)},
    )

    assert cli.main(["watch", "--repo", str(repository), "--task-id", "waiting-task"]) == 0

    output = capsys.readouterr().out
    assert "worker service is not running" in output
    assert f"agent start --repo {repository}" in output


def test_agent_watch_default_timeout_is_bounded() -> None:
    args = cli.build_parser().parse_args(["watch"])

    assert args.timeout == 1800.0


def test_agent_task_rejects_default_branch_before_queue_mutation(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)

    result = cli.main(
        ["task", "Unsafe branch", "--repo", str(repository), "--task-id", "unsafe", "--branch", "main"]
    )

    assert result == 2
    assert "protected/default branch" in capsys.readouterr().err
    assert not (state_root / ".agent-queue").exists()


def test_agent_task_current_branch_queues_existing_clean_checkout_without_renaming_it(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    subprocess.run(["git", "switch", "-c", "custom/kc-413"], cwd=repository, check=True, capture_output=True)
    commit_all(repository)
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)

    assert cli.main(
        [
            "task",
            "Implement KC-413",
            "--repo",
            str(repository),
            "--task-id",
            "kc-413",
            "--current-branch",
            "--json",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["branch"] == "custom/kc-413"
    assert result["workspace_mode"] == "checkout"
    assert subprocess.run(
        ["git", "branch", "--show-current"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip() == "custom/kc-413"
    assert not (state_root / ".agent-worktrees").exists()
    with sqlite3.connect(state_root / ".agent-queue" / "tasks.db") as connection:
        payload = json.loads(connection.execute("SELECT payload_json FROM tasks").fetchone()[0])
    assert payload["workspace_mode"] == "checkout"
    assert payload["checkout_path"] == str(repository.resolve())
    assert payload["task_branch"] == "custom/kc-413"
    assert payload["branch_owner_run_id"] == result["run_id"] == payload["run_id"]
    assert payload["branch"] == "custom/kc-413"


def test_agent_task_current_branch_refuses_dirty_checkout_before_queue_mutation(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    subprocess.run(["git", "switch", "-c", "feature/current"], cwd=repository, check=True, capture_output=True)
    commit_all(repository)
    (repository / "README.md").write_text("dirty\n", encoding="utf-8")
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)

    result = cli.main(
        ["task", "Unsafe dirty run", "--repo", str(repository), "--current-branch"]
    )

    assert result == 2
    assert "uncommitted changes" in capsys.readouterr().err
    assert not (state_root / ".agent-queue").exists()


def test_agent_task_current_branch_refuses_default_branch(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    commit_all(repository)
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)

    result = cli.main(["task", "Unsafe default", "--repo", str(repository), "--current-branch"])

    assert result == 2
    assert "protected or configured as the default branch" in capsys.readouterr().err
    assert not (state_root / ".agent-queue").exists()


def test_agent_doctor_reports_installation_without_mutating_queue(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    state_root = configure_temporary_harness(monkeypatch, tmp_path)
    fake_codex = tmp_path / "codex"
    fake_codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_codex.chmod(0o755)
    monkeypatch.setenv("AGENT_CODEX_CLI_COMMAND", str(fake_codex))

    assert cli.main(["doctor", "--repo", str(repository), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "pass"
    assert {check["name"] for check in result["checks"]} >= {
        "repository",
        "harness",
        "project_config",
        "git_repository",
        "codex_cli",
        "python_runtime",
        "worker_service",
    }
    assert not (state_root / ".agent-queue").exists()


def test_agent_task_reports_missing_runtime_dependency_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    original_import = cli.importlib.import_module

    def fail_event_import(name: str) -> object:
        if name == "event_ingestion":
            raise ModuleNotFoundError("No module named 'opentelemetry'", name="opentelemetry")
        return original_import(name)

    monkeypatch.setattr(cli.importlib, "import_module", fail_event_import)

    result = cli.main(["task", "Explain failure", "--repo", str(repository)])
    error = capsys.readouterr().err

    assert result == 2
    assert "opentelemetry" in error
    assert "agent update" in error
    assert "Traceback" not in error


def test_agent_update_refreshes_clean_git_source_and_restarts_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    source = tmp_path / "downloaded-system"
    source.mkdir()
    (source / ".git").mkdir()
    (source / "pyproject.toml").write_text(
        '[project]\nname = "ai-harness"\n',
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def run(command: object, *, label: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
        values = [str(item) for item in command]
        commands.append(values)
        stdout = "agent 0.1.0\n" if values[-1:] == ["--version"] else ""
        return subprocess.CompletedProcess(values, 0, stdout, "")

    monkeypatch.setattr(cli, "pipx_executable", lambda: "/tools/pipx")
    monkeypatch.setattr(cli, "pipx_installed_source", lambda _pipx: str(source))
    monkeypatch.setattr(cli, "installed_agent_executable", lambda: "/tools/agent")
    monkeypatch.setattr(cli, "pause_worker_for_update", lambda: (None, False))
    monkeypatch.setattr(cli, "update_process", run)

    assert cli.main(["update", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result == {
        "status": "updated",
        "version": "agent 0.1.0",
        "source": "Git checkout",
        "git_updated": True,
        "worker_restarted": True,
    }
    assert ["git", "-C", str(source), "pull", "--ff-only"] in commands
    assert ["/tools/pipx", "install", "--force", str(source)] in commands
    assert ["/tools/agent", "worker", "restart", "--json"] in commands


def test_agent_update_refuses_dirty_downloaded_system(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    source = tmp_path / "downloaded-system"
    source.mkdir()
    (source / ".git").mkdir()
    (source / "pyproject.toml").write_text(
        '[project]\nname = "ai-harness"\n',
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def run(command: object, *, label: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
        values = [str(item) for item in command]
        commands.append(values)
        if "status" in values:
            return subprocess.CompletedProcess(values, 0, " M ai_harness/cli.py\n", "")
        return subprocess.CompletedProcess(values, 0, "", "")

    monkeypatch.setattr(cli, "pipx_executable", lambda: "/tools/pipx")
    monkeypatch.setattr(cli, "pipx_installed_source", lambda _pipx: str(source))
    monkeypatch.setattr(cli, "pause_worker_for_update", lambda: (None, False))
    monkeypatch.setattr(cli, "update_process", run)

    assert cli.main(["update"]) == 2
    error = capsys.readouterr().err

    assert "update stopped" in error
    assert "ai_harness/cli.py" in error
    assert not any("install" in command or "upgrade" in command for command in commands)


def test_agent_update_uses_remote_package_source_without_git_checkout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    commands: list[list[str]] = []

    def run(command: object, *, label: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
        values = [str(item) for item in command]
        commands.append(values)
        stdout = "agent 0.1.0\n" if values[-1:] == ["--version"] else ""
        return subprocess.CompletedProcess(values, 0, stdout, "")

    monkeypatch.setattr(cli, "pipx_executable", lambda: "/tools/pipx")
    monkeypatch.setattr(
        cli,
        "pipx_installed_source",
        lambda _pipx: "git+https://example.invalid/agents.git",
    )
    monkeypatch.setattr(cli, "installed_agent_executable", lambda: "/tools/agent")
    monkeypatch.setattr(cli, "pause_worker_for_update", lambda: (None, False))
    monkeypatch.setattr(cli, "update_process", run)

    assert cli.main(["update", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["source"] == "installed package source"
    assert result["git_updated"] is False
    assert ["/tools/pipx", "upgrade", "--force", "ai-harness"] in commands


def test_agent_update_moves_downloaded_folder_to_official_update_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    source = tmp_path / "unzipped-system"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[project]\nname = "ai-harness"\n',
        encoding="utf-8",
    )
    commands: list[list[str]] = []

    def run(command: object, *, label: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
        values = [str(item) for item in command]
        commands.append(values)
        stdout = "agent 0.1.0\n" if values[-1:] == ["--version"] else ""
        return subprocess.CompletedProcess(values, 0, stdout, "")

    monkeypatch.setattr(cli, "pipx_executable", lambda: "/tools/pipx")
    monkeypatch.setattr(cli, "pipx_installed_source", lambda _pipx: str(source))
    monkeypatch.setattr(cli, "installed_agent_executable", lambda: "/tools/agent")
    monkeypatch.setattr(cli, "pause_worker_for_update", lambda: (None, False))
    monkeypatch.setattr(cli, "update_process", run)

    assert cli.main(["update", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)

    assert result["source"] == "official repository"
    assert [
        "/tools/pipx",
        "install",
        "--force",
        cli.DEFAULT_UPDATE_SOURCE,
    ] in commands
    assert not any(command[0] == "git" for command in commands)


def test_agent_update_reports_worker_restart_warning_after_package_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    def run(command: object, *, label: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
        values = [str(item) for item in command]
        if values[-2:] == ["restart", "--json"]:
            raise cli.CLIError("restarting the background service failed: unavailable")
        stdout = "agent 0.1.0\n" if values[-1:] == ["--version"] else ""
        return subprocess.CompletedProcess(values, 0, stdout, "")

    monkeypatch.setattr(cli, "pipx_executable", lambda: "/tools/pipx")
    monkeypatch.setattr(
        cli,
        "pipx_installed_source",
        lambda _pipx: "git+https://example.invalid/agents.git",
    )
    monkeypatch.setattr(cli, "installed_agent_executable", lambda: "/tools/agent")
    monkeypatch.setattr(cli, "pause_worker_for_update", lambda: (None, False))
    monkeypatch.setattr(cli, "update_process", run)

    assert cli.main(["update", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)

    assert result["status"] == "updated_with_warning"
    assert result["worker_restarted"] is False
    assert "background service" in result["warning"]


def test_agent_update_restores_previous_worker_when_package_install_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    restored: list[tuple[Path | None, bool]] = []

    def run(command: object, *, label: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
        values = [str(item) for item in command]
        if "upgrade" in values:
            raise cli.CLIError("installing the update failed: unavailable")
        return subprocess.CompletedProcess(values, 0, "", "")

    def restore(root: Path | None, was_running: bool) -> str:
        restored.append((root, was_running))
        return "; the previous worker was restarted"

    worker_root = tmp_path / "harness"
    monkeypatch.setattr(cli, "pipx_executable", lambda: "/tools/pipx")
    monkeypatch.setattr(
        cli,
        "pipx_installed_source",
        lambda _pipx: "git+https://example.invalid/agents.git",
    )
    monkeypatch.setattr(cli, "pause_worker_for_update", lambda: (worker_root, True))
    monkeypatch.setattr(cli, "restore_worker_after_failed_update", restore)
    monkeypatch.setattr(cli, "update_process", run)

    assert cli.main(["update"]) == 2
    error = capsys.readouterr().err

    assert restored == [(worker_root, True)]
    assert "previous worker was restarted" in error


def test_agent_start_validates_project_and_starts_workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    initialize_git_repository(repository)
    assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    monkeypatch.setattr(cli, "missing_runtime_imports", lambda: [])
    monkeypatch.setattr(
        cli,
        "run_worker_command",
        lambda _root, action, workers=0: {"status": "starting", "pid": 4321, "action": action, "workers": workers},
    )

    assert cli.main(["start", "--repo", str(repository), "--workers", "2"]) == 0
    output = capsys.readouterr().out

    assert "AI Harness started" in output
    assert 'Next: agent task "Describe the change"' in output


def test_local_project_config_allows_execution_identity_but_has_no_publication_grants(
    tmp_path: Path,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    assert cli.main(["init", "--repo", str(repository), "--profile", "django"]) == 0
    capsys.readouterr()

    record = load_local_project_record(repository)

    assert record is not None
    assert record.source == "local_project_config"
    assert record.project_profile == "django"
    assert record.expected_remotes == ()
    assert record.protected_paths == ()
    assert record.allowed_branch_prefixes == ("feat/",)
    resolved, errors = resolve_registry_record(repository, "django")
    assert errors == []
    assert resolved == record


def test_committed_project_config_without_user_local_trust_is_not_execution_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: object,
) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    assert cli.main(["init", "--repo", str(repository), "--profile", "django"]) == 0
    capsys.readouterr()
    monkeypatch.setenv("AI_HARNESS_CONFIG_HOME", str(tmp_path / "different-user-config"))

    record = load_local_project_record(repository)
    resolved, errors = resolve_registry_record(repository, "django")

    assert record is None
    assert resolved is None
    assert errors == ["repository is not centrally registered or locally initialized; run `agent init` first"]


def test_generated_project_config_matches_public_schema(tmp_path: Path, capsys: object) -> None:
    repository = tmp_path / "project"
    repository.mkdir()
    assert cli.main(["init", "--repo", str(repository)]) == 0
    capsys.readouterr()
    document = yaml.safe_load((repository / ".agent" / "project.yaml").read_text(encoding="utf-8"))
    schema = load_json(ROOT / "schemas" / "project_local_config.schema.json")

    assert validate_contract(document, schema, "project_config") == []
    assert document["version"] == 1
    assert document["project"]["repository"] == "."
    assert document["runtime"] == {"provider": "codex-sdk"}


def test_python_module_exposes_agent_version() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "ai_harness", "--version"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "agent 0.2.0"


@pytest.mark.parametrize(
    ("argv", "command", "run_id"),
    [
        (["failures"], "failures", ""),
        (["dead-letters"], "dead-letters", None),
        (["retry", "run-123"], "retry", "run-123"),
        (["resume", "run-123"], "resume", "run-123"),
        (["abort", "run-123"], "abort", "run-123"),
    ],
)
def test_parser_exposes_recovery_commands(
    argv: list[str],
    command: str,
    run_id: str | None,
) -> None:
    args = cli.build_parser().parse_args(argv)

    assert args.command == command
    assert getattr(args, "run_id", None) == run_id


def test_retry_checkpoint_reruns_current_role_instead_of_cached_blocker(tmp_path: Path) -> None:
    run = tmp_path / "run"
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    workflow = {"current_role": "quality-runner"}
    checkpoint_path = run / "checkpoints" / "quality-runner.json"
    checkpoint_path.parent.mkdir(parents=True)
    checkpoint_path.write_text(
        json.dumps(
            {
                "run_id": "run-123",
                "role": "quality-runner",
                "state": "role_validating",
                "attempt": 2,
                "worktree": str(worktree),
                "input_fingerprint": "task-fingerprint",
                "output_fingerprint": "sha256:blocked-result",
                "artifacts": ["quality.json"],
                "side_effects": [],
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    cli.reset_role_checkpoint_for_rerun(run, workflow)

    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert workflow["resume_role"] == "quality-runner"
    assert checkpoint["state"] == "role_pending"
    assert checkpoint["attempt"] == 2
    assert checkpoint["output_fingerprint"] == ""
    assert checkpoint["artifacts"] == []


@pytest.mark.parametrize("worker_command", ["status", "start", "restart", "stop"])
def test_parser_exposes_worker_service_commands(worker_command: str) -> None:
    args = cli.build_parser().parse_args(["worker", worker_command])

    assert args.command == "worker"
    assert args.worker_command == worker_command

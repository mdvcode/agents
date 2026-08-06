from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml
import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from ai_harness import cli
from ai_harness.project import load_project_config
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
    monkeypatch.setattr(cli, "harness_home", lambda: state_root)
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
    assert config.runtime_provider == "codex-cli"
    assert Path(result["local_trust"]).is_file()
    assert (repository / "AGENTS.md").read_text(encoding="utf-8") == existing_agents

    assert cli.main(["init", "--repo", str(repository), "--json"]) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["created"] == {"project_config": False, "agents_md": False}


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
    assert first["workspace_mode"] == "current_branch"
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
    assert result["workspace_mode"] == "current_branch"
    assert result["worker"] == {"status": "starting", "pid": 9876}
    assert worker_calls == [("start", 3)]
    assert subprocess.run(
        ["git", "branch", "--show-current"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip() == "release/2026/release-notes"
    assert not (state_root / ".agent-worktrees").exists()


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

    assert result["workspace_mode"] == "isolated"
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
    }


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
    assert (run_dir / "human-input.json").stat().st_mode & 0o777 == 0o600
    approval = json.loads((run_dir / "artifacts" / "approval.json").read_text(encoding="utf-8"))
    resumed = json.loads((run_dir / "workflow.json").read_text(encoding="utf-8"))
    assert approval["status"] == "consumed"
    assert resumed["execution_status"] == "resuming"
    assert resumed["resume_role"] == "planner"
    assert "attention" not in resumed
    assert resumed["blockers"] == []
    assert resumed["attention_history"][-1]["resolution"] == "answer_recorded"


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
                    "action": "answer_or_approve",
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
    assert "agent answer" in status_output
    assert "agent approve" not in status_output

    assert cli.main(
        ["watch", "--repo", str(repository), "--task-id", "question-task", "--timeout", "1"]
    ) == 0
    watch_output = capsys.readouterr().out
    assert "ATTENTION REQUIRED: question-task" in watch_output
    assert "agent answer" in watch_output


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
    assert result["workspace_mode"] == "current_branch"
    assert subprocess.run(
        ["git", "branch", "--show-current"], cwd=repository, check=True, capture_output=True, text=True
    ).stdout.strip() == "custom/kc-413"
    assert not (state_root / ".agent-worktrees").exists()
    with sqlite3.connect(state_root / ".agent-queue" / "tasks.db") as connection:
        payload = json.loads(connection.execute("SELECT payload_json FROM tasks").fetchone()[0])
    assert payload["workspace_mode"] == "current_branch"
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
    assert document["runtime"] == {"provider": "codex-cli"}


def test_python_module_exposes_agent_version() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "ai_harness", "--version"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "agent 0.1.0"


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


@pytest.mark.parametrize("worker_command", ["status", "start", "restart", "stop"])
def test_parser_exposes_worker_service_commands(worker_command: str) -> None:
    args = cli.build_parser().parse_args(["worker", worker_command])

    assert args.command == "worker"
    assert args.worker_command == worker_command

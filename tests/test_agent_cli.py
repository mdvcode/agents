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


def configure_temporary_harness(monkeypatch: object, tmp_path: Path) -> Path:
    state_root = tmp_path / "harness-state"
    state_root.mkdir()
    monkeypatch.setattr(cli, "harness_home", lambda: state_root)
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


def test_agent_task_is_high_level_idempotent_queue_intake(
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
    assert first["branch"] == "tast/fix-login"
    db_path = state_root / ".agent-queue" / "tasks.db"
    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    assert count == 1
    assert len(list((state_root / ".agent-queue" / "events").glob("*.json"))) == 1


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
    configure_temporary_harness(monkeypatch, tmp_path)
    assert cli.main(["task", "Mine", "--repo", str(repository), "--task-id", "mine"]) == 0
    assert cli.main(["task", "Other", "--repo", str(other), "--task-id", "other"]) == 0
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
        "worker_service",
    }
    assert not (state_root / ".agent-queue").exists()


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
    assert record.allowed_branch_prefixes == ("tast/",)
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

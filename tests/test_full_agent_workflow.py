from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agent_role_runner.py"
SPEC = importlib.util.spec_from_file_location("agent_role_runner_full", MODULE_PATH)
assert SPEC is not None
agent_role_runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = agent_role_runner
SPEC.loader.exec_module(agent_role_runner)

from test_agent_role_runner import fake_adapter_script


def git(repo: Path, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout


def test_full_workflow_uses_one_run_id_and_task_worktree(tmp_path: Path, monkeypatch: object) -> None:
    origin = tmp_path / "origin.git"
    source = tmp_path / "source"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(origin), str(source)], check=True, capture_output=True)
    git(source, "config", "user.name", "Test")
    git(source, "config", "user.email", "test@example.com")
    (source / "README.md").write_text("base\n", encoding="utf-8")
    git(source, "add", "README.md")
    git(source, "commit", "-m", "initial")
    git(source, "branch", "-M", "main")
    git(source, "push", "-u", "origin", "main")
    monkeypatch.setattr(agent_role_runner, "RUNS", tmp_path / ".agent-runs")
    command = fake_adapter_script(tmp_path / "fake_adapter.py")

    state = agent_role_runner.run_roles(
        run_id="run-integration",
        artifacts_dir=tmp_path / ".agent-runs" / "run-integration" / "artifacts",
        repository=source,
        task_id="issue-123",
        branch="issue/123",
        adapter_command=command,
        create_task_worktree=True,
        dry_run=True,
    )

    assert state["execution_status"] == "completed"
    worktree = Path(state["worktree"])
    assert worktree != source
    assert (worktree / "impl.txt").exists()
    assert not (source / "impl.txt").exists()
    assert state["run_id"] == "run-integration"
    assert (tmp_path / ".agent-runs" / "run-integration" / "worktree.json").exists()


def test_low_medium_workflow_reaches_publication_with_same_run_context(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    adapter = tmp_path / "continue_adapter.py"
    adapter.write_text(
        """
from pathlib import Path
import json
import sys

request = json.loads(sys.stdin.read())
role = request["role"]
artifacts = Path(request["artifacts_dir"])
if role == "risk-classifier":
    (artifacts / "risk.json").write_text(json.dumps({
        "risk_class": "medium",
        "reasons": [],
        "changed_areas": [],
        "high_risk_triggers": [],
        "protected_paths_touched": [],
        "protected_actions_required": [],
        "autonomy_allowed": {
            "patch": True,
            "commit": True,
            "push": True,
            "open_pr": True,
            "update_pr": True,
            "auto_merge": False,
            "deploy_staging": False,
            "deploy_production": False
        }
    }), encoding="utf-8")
elif role == "planner":
    (artifacts / "plan.md").write_text("# Plan\\n", encoding="utf-8")
elif role == "quality-runner":
    (artifacts / "quality.json").write_text(json.dumps({
        "task": "test",
        "project_profile": request["project_profile"],
        "overall_status": "pass",
        "checks": [],
        "commands_attempted": [],
        "focused_tests_passed": True,
        "repository_checks_passed": True,
        "coverage": "not measured",
        "warnings": []
    }), encoding="utf-8")
elif role == "security-agent":
    (artifacts / "security.md").write_text("# Security\\nNo issues.\\n", encoding="utf-8")
elif role == "frontend-qa-agent":
    (artifacts / "frontend_qa.json").write_text(json.dumps({
        "evidence_required": False,
        "evidence_collected": False,
        "screenshots": [],
        "console_errors": [],
        "network_errors": [],
        "blockers": [],
        "local_url": "",
        "dev_server": {},
        "next_action": "continue"
    }), encoding="utf-8")
elif role == "architecture-consistency-agent":
    (artifacts / "architecture_consistency.json").write_text(json.dumps({
        "consistency_status": "pass",
        "findings": [],
        "protected_boundaries": [],
        "recommended_repairs": [],
        "next_action": "continue"
    }), encoding="utf-8")
elif role == "semantic-conflict-agent":
    (artifacts / "semantic_conflict.json").write_text(json.dumps({
        "conflicts": [],
        "risk_level": "low",
        "required_resolution": [],
        "next_action": "continue"
    }), encoding="utf-8")
elif role == "reviewer":
    (artifacts / "review.md").write_text("# Review\\nNo findings.\\n", encoding="utf-8")
elif role == "orchestrator":
    (artifacts / "verdict.json").write_text(json.dumps({
        "decision": "publish_pr",
        "execution_status": "completed",
        "task": "test",
        "project_profile": request["project_profile"],
        "risk_class": "medium",
        "checks_attempted": True,
        "checks_passed": True,
        "blockers": [],
        "warnings": [],
        "high_risk_triggers": [],
        "protected_paths_touched": [],
        "publication_result": {
            "commit_created": False,
            "branch_pushed": False,
            "pr_created_or_updated": False,
            "pr_url": "",
            "pr_state": "not_created"
        },
        "flowfox_visual_evidence": {
            "required": False,
            "provided": False,
            "items": []
        },
        "approval_required_before_publish": False,
        "approval_required_before_merge": True,
        "reasoning_summary": [],
        "next_actions": [],
        "lessons_updated": False
    }), encoding="utf-8")
elif role == "eval-runner":
    (artifacts / "eval_runner.json").write_text(json.dumps({
        "evals_run": [],
        "regressions": [],
        "coverage_gaps": [],
        "next_action": "continue"
    }), encoding="utf-8")
elif role == "report-agent":
    (artifacts / "report.md").write_text("# Report\\nDone.\\n", encoding="utf-8")
print(json.dumps({
    "status": "completed",
    "next_action": "continue",
    "summary": f"{role} done",
    "artifacts_created": [],
    "blockers": [],
    "warnings": [],
    "tokens_used": 1
}))
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_role_runner, "RUNS", tmp_path / ".agent-runs")
    calls: list[dict[str, object]] = []

    def fake_publication(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "status": "completed",
            "next_action": "completed",
            "summary": "published",
            "artifacts_created": ["publication.json"],
            "blockers": [],
            "warnings": [],
            "tokens_used": 0,
        }

    monkeypatch.setattr(agent_role_runner, "run_publication", fake_publication)

    state = agent_role_runner.run_roles(
        run_id="run-publish",
        artifacts_dir=tmp_path / ".agent-runs" / "run-publish" / "artifacts",
        repository=tmp_path,
        adapter_command=f"{sys.executable} {adapter}",
        dry_run=True,
    )

    assert state["execution_status"] == "completed"
    assert state["roles"][-1]["role"] == "publication"
    assert calls
    assert calls[0]["run_id"] == "run-publish"
    assert calls[0]["artifacts_dir"] == tmp_path / ".agent-runs" / "run-publish" / "artifacts"


def test_high_risk_stops_before_publication(tmp_path: Path, monkeypatch: object) -> None:
    adapter = tmp_path / "high_risk_adapter.py"
    adapter.write_text(
        """
from pathlib import Path
import json
import sys

request = json.loads(sys.stdin.read())
role = request["role"]
artifacts = Path(request["artifacts_dir"])
if role == "planner":
    (artifacts / "plan.md").write_text("# Plan\\n", encoding="utf-8")
elif role == "risk-classifier":
    (artifacts / "risk.json").write_text(json.dumps({
        "risk_class": "high",
        "reasons": ["protected area"],
        "changed_areas": [],
        "high_risk_triggers": ["auth"],
        "protected_paths_touched": ["auth/example.py"],
        "protected_actions_required": ["human approval"],
        "autonomy_allowed": {
            "patch": True,
            "commit": False,
            "push": False,
            "open_pr": False,
            "update_pr": False,
            "auto_merge": False,
            "deploy_staging": False,
            "deploy_production": False
        }
    }), encoding="utf-8")
print(json.dumps({
    "status": "completed",
    "next_action": "continue",
    "summary": f"{role} done",
    "artifacts_created": [],
    "blockers": [],
    "warnings": [],
    "tokens_used": 1
}))
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_role_runner, "RUNS", tmp_path / ".agent-runs")

    def forbidden_publication(**_kwargs: object) -> dict[str, object]:
        raise AssertionError("publication must not run for high risk")

    monkeypatch.setattr(agent_role_runner, "run_publication", forbidden_publication)

    state = agent_role_runner.run_roles(
        run_id="run-high",
        artifacts_dir=tmp_path / ".agent-runs" / "run-high" / "artifacts",
        repository=tmp_path,
        adapter_command=f"{sys.executable} {adapter}",
        dry_run=True,
    )

    assert state["execution_status"] == "awaiting_approval"
    assert state["roles"][-1]["role"] == "approval-gate"


def test_planner_must_create_run_scoped_plan(tmp_path: Path, monkeypatch: object) -> None:
    adapter = tmp_path / "no_plan_adapter.py"
    adapter.write_text(
        """
import json
print(json.dumps({
    "status": "completed",
    "next_action": "continue",
    "summary": "done",
    "artifacts_created": [],
    "blockers": [],
    "warnings": [],
    "tokens_used": 1
}))
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_role_runner, "RUNS", tmp_path / ".agent-runs")

    state = agent_role_runner.run_roles(
        run_id="run-no-plan",
        artifacts_dir=tmp_path / ".agent-runs" / "run-no-plan" / "artifacts",
        repository=tmp_path,
        adapter_command=f"{sys.executable} {adapter}",
        dry_run=True,
    )

    assert state["execution_status"] == "blocked"
    assert state["roles"][-1]["result"]["summary"] == "Role artifact validation failed."


def test_publication_role_invokes_publish_pr_with_run_context(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr(agent_role_runner.subprocess, "run", fake_run)

    result = agent_role_runner.run_publication(
        run_id="run-123",
        repository=tmp_path,
        artifacts_dir=tmp_path / ".agent-runs" / "run-123" / "artifacts",
        dry_run=True,
        timeout_seconds=600,
    )

    assert result["status"] == "completed"
    assert calls == [
        [
            "python3",
            "scripts/publish_pr.py",
            "--artifacts-dir",
            str((tmp_path / ".agent-runs" / "run-123" / "artifacts").resolve()),
            "--run-id",
            "run-123",
            "--repo",
            str(tmp_path.resolve()),
            "--dry-run",
        ]
    ]

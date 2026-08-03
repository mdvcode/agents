from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from approval_lifecycle import approve_run, prepare_resume  # noqa: E402
from runtimes.codex_cli import CodexCliRuntime  # noqa: E402


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agent_role_runner.py"
SPEC = importlib.util.spec_from_file_location("agent_role_runner", MODULE_PATH)
assert SPEC is not None
agent_role_runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = agent_role_runner
SPEC.loader.exec_module(agent_role_runner)


def fake_adapter_script(path: Path) -> str:
    path.write_text(
        """
from pathlib import Path
import json
import sys

request = json.loads(sys.stdin.read())
role = request["role"]
artifacts = Path(request["artifacts_dir"])
repository = Path(request["repository"])
if role == "planner":
    (artifacts / "plan.md").write_text("# Plan\\n", encoding="utf-8")
    created = ["plan.md"]
    next_action = "risk-classifier"
elif role == "risk-classifier":
    (artifacts / "risk.json").write_text(json.dumps({
        "risk_class": "medium",
        "reasons": [],
        "changed_areas": ["impl.txt"],
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
    created = ["risk.json"]
    next_action = "implementation-agent"
elif role == "implementation-agent":
    (repository / "impl.txt").write_text("implemented\\n", encoding="utf-8")
    (artifacts / "implementation.json").write_text(json.dumps({
        "changed_files": ["impl.txt"],
        "summary": "implemented"
    }), encoding="utf-8")
    created = ["implementation.json"]
    next_action = "completed"
elif role == "test-generator":
    (artifacts / "test_plan.json").write_text(json.dumps({"tests": [], "summary": "covered"}), encoding="utf-8")
    (artifacts / "test_result.json").write_text(json.dumps({"status": "pass", "summary": "covered"}), encoding="utf-8")
    created = ["test_plan.json", "test_result.json"]
    next_action = "quality-runner"
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
    created = ["quality.json"]
    next_action = "publication"
elif role == "security-agent":
    (artifacts / "security.json").write_text(json.dumps({
        "verdict": "works", "expected": [], "observed": [], "evidence": [],
        "blockers": [], "repair_required": False,
        "status": "pass", "highest_severity": "none", "project_profile": request["project_profile"], "findings": [],
        "blocker_ids": [], "secret_findings": [], "commands_attempted": [], "warnings": []
    }), encoding="utf-8")
    created = ["security.json"]
    next_action = "publication"
elif role == "reviewer":
    (artifacts / "review.json").write_text(json.dumps({
        "verdict": "works", "expected": [], "observed": [], "evidence": [],
        "blockers": [], "repair_required": False,
        "status": "pass", "project_profile": request["project_profile"], "findings": [],
        "blocker_ids": [], "policy_violations": [], "known_lesson_conflicts": [], "warnings": []
    }), encoding="utf-8")
    created = ["review.json"]
    next_action = "publication"
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
        "visual_evidence": {"required": False, "provided": False, "items": []},
        "approval_required_before_publish": False,
        "approval_required_before_merge": True,
        "reasoning_summary": [],
        "next_actions": [],
        "lessons_updated": False
    }), encoding="utf-8")
    created = ["verdict.json"]
    next_action = "publication"
else:
    created = []
    next_action = "completed"
print(json.dumps({
    "status": "completed",
    "next_action": next_action,
    "summary": f"{role} done",
    "artifacts_created": created,
    "blockers": [],
    "warnings": [],
    "tokens_used": 7
}))
""".lstrip(),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return f"{sys.executable} {path}"


def add_local_origin(repo: Path) -> None:
    origin = repo.parent / f"{repo.name}-origin.git"
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", str(origin)], cwd=repo, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=repo, check=True, capture_output=True)


def test_role_result_accepts_free_form_advisory_next_action() -> None:
    result = {
        "status": "completed",
        "next_action": "Inspect the branch baseline before implementation.",
        "summary": "Planning completed.",
        "artifacts_created": ["plan.md"],
        "blockers": [],
        "warnings": [],
        "tokens_used": 1,
    }

    assert agent_role_runner.validate_role_result(result, "planner") == []


def test_role_budget_tokens_excludes_cached_input() -> None:
    result = {
        "tokens_used": 782_548,
        "input_tokens": 772_746,
        "cached_input_tokens": 721_920,
        "output_tokens": 9_802,
    }

    assert agent_role_runner.role_budget_tokens(result) == 60_628


def test_resume_production_runtime_reloads_trusted_command() -> None:
    stored = {
        "provider": "codex-cli",
        "production": True,
        "command": "python3 scripts/adapters/codex_cli_executor.py",
    }

    assert agent_role_runner.resume_runtime_command(stored) == ""


def test_resume_fixture_runtime_reuses_stored_command() -> None:
    stored = {
        "provider": "test-subprocess",
        "production": False,
        "command": "python fake_adapter.py",
    }

    assert agent_role_runner.resume_runtime_command(stored) == "python fake_adapter.py"


def test_agent_role_runner_preflights_configured_runtime_before_roles(tmp_path: Path, monkeypatch: object) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True, capture_output=True)
    add_local_origin(tmp_path)
    monkeypatch.setattr(agent_role_runner, "RUNS", tmp_path / ".agent-runs")
    monkeypatch.delenv("AGENT_RUNTIME_PROVIDER", raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_COMMAND", raising=False)
    calls: list[Path] = []

    def fake_preflight(self: object, *, worktree: Path, timeout_seconds: int) -> dict[str, object]:
        calls.append(worktree)
        return {
            "execution_status": "blocked",
            "blockers": ["Codex CLI is not available or not authenticated."],
            "warnings": [],
        }

    monkeypatch.setattr(CodexCliRuntime, "preflight", fake_preflight)

    state = agent_role_runner.run_roles(
        run_id="run-1",
        repository=tmp_path,
        dry_run=True,
        create_task_worktree=True,
    )

    assert state["execution_status"] == "blocked"
    assert state["roles"] == []
    assert len(calls) == 1
    assert calls[0].parent.name == ".agent-worktrees"
    assert state["blockers"] == ["Codex CLI is not available or not authenticated."]
    assert state["runtime"]["provider"] == "codex-cli"


def test_agent_role_runner_invokes_adapter_for_core_roles(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(agent_role_runner, "RUNS", tmp_path / ".agent-runs")
    command = fake_adapter_script(tmp_path / "fake_adapter.py")

    state = agent_role_runner.run_roles(
        run_id="run-2",
        repository=tmp_path,
        adapter_command=command,
        dry_run=True,
    )

    assert state["execution_status"] == "awaiting_approval"
    assert [item["role"] for item in state["roles"]][:10] == [
        "issue-intake",
        "context-compiler",
        "planner",
        "risk-classifier",
        "implementation-agent",
        "test-generator",
        "quality-runner",
        "security-agent",
        "reviewer",
        "orchestrator",
    ]
    artifacts = tmp_path / ".agent-runs" / "run-2" / "artifacts"
    assert (tmp_path / ".agent-runs" / "run-2" / "role-results" / "planner-1.json").exists()
    assert (artifacts / "risk.json").exists()
    assert (tmp_path / "impl.txt").read_text(encoding="utf-8") == "implemented\n"
    assert (tmp_path / ".agent-runs" / "run-2" / "role-requests" / "planner.json").exists()
    assert (tmp_path / ".agent-runs" / "run-2" / "context-manifests" / "planner.json").exists()
    request = json.loads((tmp_path / ".agent-runs" / "run-2" / "role-requests" / "planner.json").read_text(encoding="utf-8"))
    assert request["prompt_path"] == ".agents/prompts/planner.md"
    assert request["output_contract"] == "schemas/roles/planner.schema.json"
    assert request["project_profile"] == "agent_workspace"
    assert request["expected_artifacts"] == ["plan.md", "project_profile.json"]
    assert request["filesystem_access"] == "read_only"
    assert request["allowed_tools"] == ["filesystem_read", "repository_search"]


def test_approved_run_resumes_same_worktree_from_checkpoint(tmp_path: Path, monkeypatch: object) -> None:
    runs = tmp_path / ".agent-runs"
    monkeypatch.setattr(agent_role_runner, "RUNS", runs)
    command = fake_adapter_script(tmp_path / "fake_adapter.py")
    first = agent_role_runner.run_roles(
        run_id="resume-checkpoint",
        repository=tmp_path,
        adapter_command=command,
        dry_run=True,
    )
    run_dir = runs / "resume-checkpoint"
    approval = json.loads((run_dir / "artifacts" / "approval.json").read_text(encoding="utf-8"))
    approve_run(run_dir, actor="human-reviewer")
    prepare_resume(run_dir)

    resumed = agent_role_runner.run_roles(run_id="resume-checkpoint", resume=True, dry_run=True)

    assert first["execution_status"] == "awaiting_approval"
    assert resumed["worktree"] == first["worktree"]
    assert resumed["resume_count"] == 1
    checkpoint_role = approval["checkpoint_role"]
    assert (run_dir / "role-results" / f"{checkpoint_role}-1.json").exists()
    assert (run_dir / "role-results" / f"{checkpoint_role}-2.json").exists()
    renewed = json.loads((run_dir / "artifacts" / "approval.json").read_text(encoding="utf-8"))
    assert renewed["approval_id"] != approval["approval_id"]
    assert "Workflow blockers" in renewed["reason"]


def test_unfinished_run_cannot_be_restarted_without_resume(tmp_path: Path, monkeypatch: object) -> None:
    runs = tmp_path / ".agent-runs"
    monkeypatch.setattr(agent_role_runner, "RUNS", runs)
    command = fake_adapter_script(tmp_path / "fake_adapter.py")
    first = agent_role_runner.run_roles(
        run_id="must-resume",
        repository=tmp_path,
        adapter_command=command,
        dry_run=True,
    )
    approval_before = (runs / "must-resume" / "artifacts" / "approval.json").read_text(encoding="utf-8")

    repeated = agent_role_runner.run_roles(
        run_id="must-resume",
        repository=tmp_path,
        adapter_command=command,
        dry_run=True,
    )

    assert repeated == first
    assert (runs / "must-resume" / "artifacts" / "approval.json").read_text(encoding="utf-8") == approval_before


def test_implementation_artifact_validation_detects_source_repo_mutation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    worktree = tmp_path / "worktree"
    source.mkdir()
    worktree.mkdir()
    subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=source, check=True)
    (source / "file.txt").write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "add", "file.txt"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=source, check=True, capture_output=True, text=True)
    before = agent_role_runner.git_snapshot(source)
    (source / "file.txt").write_text("after\n", encoding="utf-8")

    errors = agent_role_runner.validate_role_artifacts(
        role="implementation-agent",
        result={
            "status": "completed",
            "next_action": "continue",
            "summary": "done",
            "artifacts_created": [],
            "blockers": [],
            "warnings": [],
            "tokens_used": 1,
        },
        artifacts_dir=tmp_path / "artifacts",
        worktree=worktree,
        source_repository=source,
        source_snapshot_before=before,
        create_task_worktree=True,
    )

    assert "implementation-agent changed the source repository instead of only the task worktree" in errors


def test_adapter_role_cannot_claim_foreign_artifact(tmp_path: Path) -> None:
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "plan.md").write_text("# Plan\n", encoding="utf-8")
    (tmp_path / "artifacts" / "verdict.json").write_text("{}", encoding="utf-8")

    errors = agent_role_runner.validate_role_artifacts(
        role="planner",
        result={
            "status": "completed",
            "next_action": "continue",
            "summary": "done",
            "artifacts_created": ["plan.md", "verdict.json"],
            "blockers": [],
            "warnings": [],
            "tokens_used": 1,
        },
        artifacts_dir=tmp_path / "artifacts",
        worktree=tmp_path,
        source_repository=tmp_path,
        source_snapshot_before="",
        create_task_worktree=False,
    )

    assert "planner cannot claim artifact it does not own: verdict.json" in errors


@pytest.mark.parametrize("adapter_status", ["completed", "blocked"])
def test_runner_blocks_direct_foreign_artifact_overwrite(
    tmp_path: Path,
    monkeypatch: object,
    adapter_status: str,
) -> None:
    adapter = tmp_path / "malicious_adapter.py"
    adapter.write_text(
        """
from pathlib import Path
import json
import sys
request = json.loads(sys.stdin.read())
artifacts = Path(request["artifacts_dir"])
if request["role"] == "planner":
    (artifacts / "plan.md").write_text("# Plan\\n", encoding="utf-8")
    (artifacts / "verdict.json").write_text("{}\\n", encoding="utf-8")
print(json.dumps({
    "status": sys.argv[1], "next_action": "continue", "summary": "done",
    "artifacts_created": ["plan.md"], "blockers": [], "warnings": [], "tokens_used": 1
}))
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(agent_role_runner, "RUNS", tmp_path / ".agent-runs")
    state = agent_role_runner.run_roles(
        run_id="ownership-run",
        repository=tmp_path,
        adapter_command=f"{sys.executable} {adapter} {adapter_status}",
        dry_run=True,
    )
    assert state["execution_status"] == "awaiting_approval"
    planner = next(item for item in state["roles"] if item["role"] == "planner")
    assert planner["result"]["summary"] == "Role artifact ownership validation failed."
    assert not (tmp_path / ".agent-runs" / "ownership-run" / "artifacts" / "verdict.json").exists()
    errors_path = tmp_path / ".agent-runs" / "ownership-run" / "errors.jsonl"
    assert "ROLE_NOT_COMPLETED" in errors_path.read_text(encoding="utf-8")


def test_frontend_qa_preflight_marks_evidence_unavailable(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.delenv("AGENT_BROWSER_AVAILABLE", raising=False)
    monkeypatch.setattr(agent_role_runner, "RUNS", tmp_path / ".agent-runs")

    result = agent_role_runner.preflight_role_execution(
        role="frontend-qa-agent",
        project_profile="nextjs_web",
        artifacts_dir=tmp_path / "artifacts",
        dry_run=True,
    )

    assert result is not None
    assert result["status"] == "completed"
    assert result["artifacts_created"] == ["frontend_qa.json"]
    artifact = json.loads((tmp_path / "artifacts" / "frontend_qa.json").read_text(encoding="utf-8"))
    assert artifact["evidence_required"] is True
    assert artifact["evidence_collected"] is False
    assert artifact["blockers"]


def test_frontend_qa_preflight_preserves_valid_external_evidence(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.delenv("AGENT_BROWSER_AVAILABLE", raising=False)
    artifacts = tmp_path / "artifacts"
    screenshot = artifacts / "frontend-evidence" / "desktop.png"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"png")
    payload = {
        "verdict": "works",
        "expected": ["cards are readable"],
        "observed": ["cards are readable"],
        "evidence": ["desktop and mobile interaction evidence"],
        "blockers": [],
        "repair_required": False,
        "evidence_required": True,
        "evidence_collected": True,
        "screenshots": ["frontend-evidence/desktop.png"],
        "console_errors": [],
        "network_errors": [],
        "local_url": "http://127.0.0.1:4173/#leistungen",
        "dev_server": {"command": "python3 -m http.server 4173", "status": "stopped"},
        "next_action": "continue",
    }
    (artifacts / "frontend_qa.json").write_text(json.dumps(payload), encoding="utf-8")

    result = agent_role_runner.preflight_role_execution(
        role="frontend-qa-agent",
        project_profile="nextjs_web",
        artifacts_dir=artifacts,
        dry_run=True,
    )

    assert result is not None
    assert result["status"] == "completed"
    assert result["artifacts_created"] == []
    assert json.loads((artifacts / "frontend_qa.json").read_text(encoding="utf-8")) == payload


def test_frontend_verifier_works_requires_real_run_scoped_evidence(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    screenshot = artifacts / "frontend-evidence" / "flow.png"
    screenshot.parent.mkdir(parents=True)
    screenshot.write_bytes(b"png")
    payload = {
        "verdict": "works",
        "expected": ["save succeeds"],
        "observed": ["save succeeded"],
        "evidence": ["interaction: click save and observe confirmation"],
        "blockers": [],
        "repair_required": False,
        "evidence_required": True,
        "evidence_collected": True,
        "screenshots": ["frontend-evidence/flow.png"],
        "console_errors": [],
        "network_errors": [],
        "local_url": "http://127.0.0.1:3000/settings",
        "dev_server": {"command": "bun dev", "status": "running"},
        "next_action": "continue",
    }
    (artifacts / "frontend_qa.json").write_text(json.dumps(payload), encoding="utf-8")

    errors = agent_role_runner.validate_verifier_artifact("frontend-qa-agent", artifacts)

    assert errors == []
    payload["local_url"] = "https://example.com/settings"
    payload["screenshots"] = []
    (artifacts / "frontend_qa.json").write_text(json.dumps(payload), encoding="utf-8")
    errors = agent_role_runner.validate_verifier_artifact("frontend-qa-agent", artifacts)
    assert any("loopback" in error for error in errors)
    assert any("screenshot" in error for error in errors)


def test_security_verifier_rejects_inconsistent_severity(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    payload = {
        "verdict": "works",
        "highest_severity": "critical",
        "blockers": [],
        "repair_required": False,
    }
    (artifacts / "security.json").write_text(json.dumps(payload), encoding="utf-8")

    errors = agent_role_runner.validate_verifier_artifact("security-agent", artifacts)

    assert errors == ["security.json: works permits only none or low highest_severity"]


def test_issue_intake_checkpoint_is_a_non_llm_harness_stage(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(agent_role_runner, "RUNS", tmp_path / ".agent-runs")
    command = fake_adapter_script(tmp_path / "fake_adapter.py")
    state = agent_role_runner.run_roles(
        run_id="issue-intake-kind",
        repository=tmp_path,
        adapter_command=command,
        dry_run=True,
    )

    checkpoint = state["roles"][0]
    assert checkpoint["role"] == "issue-intake"
    assert checkpoint["execution_kind"] == "harness_stage"
    assert checkpoint["llm_invoked"] is False
    assert not (tmp_path / ".agent-runs" / "issue-intake-kind" / "role-requests" / "issue-intake.json").exists()


def test_hard_router_stop_is_recorded_as_structured_blocker(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(agent_role_runner, "RUNS", tmp_path / ".agent-runs")
    monkeypatch.setattr(
        agent_role_runner,
        "decide_next_role",
        lambda **_kwargs: {
            "next_role": "blocked",
            "reason": "A CRITICAL security finding blocks the workflow.",
            "stop": True,
            "publication_allowed": False,
            "loop": None,
            "warnings": ["SEC-CRITICAL"],
        },
    )

    state = agent_role_runner.run_roles(
        run_id="critical-security-stop",
        repository=tmp_path,
        adapter_command=fake_adapter_script(tmp_path / "fake_adapter.py"),
        dry_run=True,
    )

    assert state["execution_status"] == "blocked"
    assert state["blockers"] == [
        "A CRITICAL security finding blocks the workflow.",
        "SEC-CRITICAL",
    ]
    errors = (tmp_path / ".agent-runs" / "critical-security-stop" / "errors.jsonl").read_text(encoding="utf-8")
    assert "ROUTER_BLOCKED" in errors

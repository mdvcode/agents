from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from typing import Sequence

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "publish_pr.py"
SPEC = importlib.util.spec_from_file_location("publish_pr", MODULE_PATH)
assert SPEC is not None
publish_pr = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = publish_pr
SPEC.loader.exec_module(publish_pr)


class FakeRunner:
    def __init__(
        self,
        responses: dict[tuple[str, ...], publish_pr.CommandResult] | None = None,
    ) -> None:
        self.responses = responses or {}
        self.calls: list[tuple[str, ...]] = []

    def run(self, args: Sequence[str], cwd: Path) -> publish_pr.CommandResult:
        command = tuple(args)
        self.calls.append(command)
        return self.responses.get(command, publish_pr.CommandResult(0, "", ""))


def run_artifacts(root: Path, run_id: str = "run-test") -> Path:
    path = root / ".agent-runs" / run_id / "artifacts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def publisher_for(
    root: Path,
    *,
    runner: publish_pr.CommandRunner | FakeRunner | None = None,
    run_id: str = "run-test",
) -> publish_pr.Publisher:
    return publish_pr.Publisher(
        root=root,
        runner=runner,
        artifacts_dir=run_artifacts(root, run_id),
        run_id=run_id,
    )


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    return result.stdout.strip()


def write_json(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def test_reconcile_side_effects_recovers_commit_push_and_pr_after_crash(tmp_path: Path) -> None:
    marker = "run-test:publication"
    runner = FakeRunner(
        {
            ("git", "log", "--format=%H%x00%B%x00", "-n", "200"): publish_pr.CommandResult(
                0, f"abc123\x00subject\n\nTask-Idempotency-Key: {marker}\x00", ""
            ),
            ("git", "rev-parse", "feat/task"): publish_pr.CommandResult(0, "abc123\n", ""),
            ("git", "ls-remote", "origin", "refs/heads/feat/task"): publish_pr.CommandResult(
                0, "abc123\trefs/heads/feat/task\n", ""
            ),
            ("gh", "pr", "view", "feat/task", "--json", "number,url"): publish_pr.CommandResult(
                0, '{"number":17,"url":"https://example/pr/17"}', ""
            ),
        }
    )
    publisher = publisher_for(tmp_path, runner=runner)
    publication = publish_pr.PublicationResult(
        run_id="run-test", branch="feat/task", idempotency_key=marker
    )

    publisher.reconcile_side_effects(tmp_path, publication)

    assert publication.commit_sha == "abc123"
    assert publication.push_completed is True
    assert publication.pr_number == 17
    assert publication.execution_status == "pr_published"
    persisted = json.loads(publisher.publication_path.read_text(encoding="utf-8"))
    assert persisted["pr_url"] == "https://example/pr/17"
    spans_path = publisher.artifacts.parent / "raw-events" / "otel-spans.jsonl"
    spans = [json.loads(line) for line in spans_path.read_text(encoding="utf-8").splitlines()]
    assert spans[-1]["name"] == "ai_harness.publication.idempotency_check"
    assert spans[-1]["attributes"]["publication.prevented_steps"] == ["commit", "push", "pr"]


@pytest.mark.parametrize(
    ("existing", "expected_step", "expected_status"),
    [
        ({}, "commit", "committed"),
        ({"commit_sha": "abc123"}, "push", "pushed"),
        ({"commit_sha": "abc123", "push_completed": True}, "pr", "pr_published"),
    ],
)
def test_each_publication_crash_boundary_reconciles_without_repeating_side_effect(
    tmp_path: Path,
    existing: dict[str, object],
    expected_step: str,
    expected_status: str,
) -> None:
    marker = "run-test:publication"
    responses: dict[tuple[str, ...], publish_pr.CommandResult] = {}
    if expected_step == "commit":
        responses[("git", "log", "--format=%H%x00%B%x00", "-n", "200")] = publish_pr.CommandResult(
            0, f"abc123\x00subject\n\nTask-Idempotency-Key: {marker}\x00", ""
        )
        responses[("git", "rev-parse", "feat/task")] = publish_pr.CommandResult(1, "", "missing")
    elif expected_step == "push":
        responses[("git", "rev-parse", "feat/task")] = publish_pr.CommandResult(0, "abc123\n", "")
        responses[("git", "ls-remote", "origin", "refs/heads/feat/task")] = publish_pr.CommandResult(
            0, "abc123\trefs/heads/feat/task\n", ""
        )
    else:
        responses[("gh", "pr", "view", "feat/task", "--json", "number,url")] = publish_pr.CommandResult(
            0, '{"number":17,"url":"https://example/pr/17"}', ""
        )
    publisher = publisher_for(tmp_path, runner=FakeRunner(responses))
    publication = publish_pr.PublicationResult(
        run_id="run-test",
        branch="feat/task",
        idempotency_key=marker,
        **existing,
    )

    publisher.reconcile_side_effects(tmp_path, publication)

    assert publication.reconciled_steps == [expected_step]
    assert publication.execution_status == expected_status


def test_publication_idempotency_telemetry_failure_is_fail_open(tmp_path: Path, monkeypatch: object) -> None:
    marker = "run-test:publication"
    runner = FakeRunner(
        {
            ("git", "log", "--format=%H%x00%B%x00", "-n", "200"): publish_pr.CommandResult(
                0, f"abc123\x00subject\n\nTask-Idempotency-Key: {marker}\x00", ""
            ),
        }
    )
    monkeypatch.setattr(
        publish_pr,
        "safe_telemetry_runtime",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("exporter unavailable")),
    )
    publisher = publisher_for(tmp_path, runner=runner)
    publication = publish_pr.PublicationResult(
        run_id="run-test",
        branch="feat/task",
        idempotency_key=marker,
    )

    publisher.reconcile_side_effects(tmp_path, publication)

    assert publication.commit_sha == "abc123"
    assert publication.reconciled_steps == ["commit"]


def risk_payload(risk_class: str = "medium") -> dict[str, object]:
    allowed = risk_class != "high"
    return {
        "risk_class": risk_class,
        "reasons": [],
        "changed_areas": ["allowed.txt"],
        "high_risk_triggers": ["protected"] if risk_class == "high" else [],
        "protected_paths_touched": [],
        "protected_actions_required": [],
        "autonomy_allowed": {
            "patch": True,
            "commit": allowed,
            "push": allowed,
            "open_pr": allowed,
            "update_pr": allowed,
            "auto_merge": False,
            "deploy_staging": False,
            "deploy_production": False,
        },
    }


def verdict_payload(risk_class: str = "medium") -> dict[str, object]:
    return {
        "decision": "await_approval" if risk_class == "high" else "publish_pr",
        "execution_status": "blocked" if risk_class == "high" else "planned",
        "task": "publication test",
        "project_profile": "agent_workspace",
        "risk_class": risk_class,
        "checks_attempted": True,
        "checks_passed": True,
        "blockers": [],
        "warnings": [],
        "high_risk_triggers": ["protected"] if risk_class == "high" else [],
        "protected_paths_touched": [],
        "visual_evidence": {"required": False, "provided": False, "items": []},
        "approval_required_before_publish": risk_class == "high",
        "approval_required_before_merge": True,
        "reasoning_summary": [],
        "next_actions": [],
        "lessons_updated": False,
    }


def quality_payload() -> dict[str, object]:
    return {
        "task": "publication test",
        "project_profile": "agent_workspace",
        "overall_status": "pass",
        "checks": [],
        "commands_attempted": [],
        "focused_tests_passed": True,
        "repository_checks_passed": True,
        "coverage": "not measured",
        "warnings": [],
    }


def test_constructor_rejects_implicit_root_artifacts(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="root artifacts"):
        publish_pr.Publisher(root=tmp_path)


def test_pr_state_ready_only_when_checks_and_evidence_pass() -> None:
    verdict = verdict_payload()
    assert publish_pr.determine_pr_state(quality_payload(), verdict) == "ready"
    verdict["visual_evidence"] = {"required": True, "provided": False, "items": []}
    assert publish_pr.determine_pr_state(quality_payload(), verdict) == "draft"


def test_irreversible_publication_resume_rejects_changed_inputs() -> None:
    resume = publish_pr.PublicationResult(
        commit_sha="abc123",
        input_fingerprint="sha256:original",
    )

    assert publish_pr.irreversible_resume_blocker(resume, "sha256:changed")
    assert publish_pr.irreversible_resume_blocker(resume, "sha256:original") == ""


def test_high_risk_blocks_publication_preflight(tmp_path: Path) -> None:
    publisher = publisher_for(tmp_path, runner=FakeRunner())
    publication = publisher.preflight(
        tmp_path,
        {},
        {},
        risk_payload("high"),
        quality_payload(),
        verdict_payload("high"),
        {"project_profile": "agent_workspace"},
        {"project_profile": "agent_workspace", "task_id": "task", "include": ["allowed.txt"]},
        True,
    )
    assert publication.execution_status == "blocked"
    assert any("not permitted" in error or "high-risk" in error for error in publication.errors)


def test_protected_and_unsafe_paths_are_rejected(tmp_path: Path) -> None:
    policy = {"projects": {"nextjs_web": {"protected_paths": [".env", "auth/**"]}}}
    blockers = publish_pr.protected_path_blockers(
        {"include": ["auth/service.py", "safe.py"]}, policy, "nextjs_web"
    )
    assert blockers == ["auth/service.py"]
    publication = publish_pr.PublicationResult()
    selected = publisher_for(tmp_path, runner=FakeRunner()).stage_change_set(
        tmp_path,
        {"include": ["../escape"], "exclude": []},
        dry_run=True,
        publication=publication,
    )
    assert selected == set()
    assert publication.errors


def test_exact_staged_set_blocks_unrelated_pre_staged_file(tmp_path: Path) -> None:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    for name in ("allowed.txt", "unrelated.txt"):
        (tmp_path / name).write_text("old\n", encoding="utf-8")
    git(tmp_path, "add", "allowed.txt", "unrelated.txt")
    git(tmp_path, "commit", "-m", "initial")
    (tmp_path / "allowed.txt").write_text("new\n", encoding="utf-8")
    (tmp_path / "unrelated.txt").write_text("new\n", encoding="utf-8")
    git(tmp_path, "add", "unrelated.txt")
    publication = publish_pr.PublicationResult()
    selected = publisher_for(tmp_path, runner=publish_pr.CommandRunner()).stage_change_set(
        tmp_path,
        {"include": ["allowed.txt"], "exclude": []},
        dry_run=False,
        publication=publication,
    )
    assert selected == set()
    assert any("pre-existing staged" in error for error in publication.errors)


def test_publication_state_does_not_mutate_orchestrator_verdict(tmp_path: Path) -> None:
    artifacts = run_artifacts(tmp_path)
    verdict = verdict_payload()
    write_json(artifacts / "verdict.json", verdict)
    publisher = publisher_for(tmp_path)
    publication = publish_pr.PublicationResult(
        run_id="run-test",
        run_dir=str(artifacts.parent),
        execution_status="completed",
        pr_created_or_updated=True,
        pr_url="https://example.test/pr/1",
        pr_state="ready",
    )
    publisher.update_artifacts(publication)
    assert json.loads((artifacts / "verdict.json").read_text(encoding="utf-8")) == verdict
    assert json.loads((artifacts / "publication.json").read_text(encoding="utf-8"))["pr_url"] == publication.pr_url


def init_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "clone", str(remote), str(repo)], check=True, capture_output=True)
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "allowed.txt").write_text("old\n", encoding="utf-8")
    git(repo, "add", "allowed.txt")
    git(repo, "commit", "-m", "initial")
    git(repo, "branch", "-M", "main")
    git(repo, "push", "-u", "origin", "main")
    main_sha = git(repo, "rev-parse", "main")
    git(repo, "checkout", "-b", "feat/step1-task")
    (repo / "allowed.txt").write_text("new\n", encoding="utf-8")
    return remote, repo, main_sha


def install_fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "gh-state"
    script = bin_dir / "gh"
    script.write_text(
        f"""#!/bin/sh
if [ "$1" = "auth" ]; then exit 0; fi
if [ "$1" = "pr" ] && [ "$2" = "view" ]; then
  if [ -f "{state}" ]; then
    printf '%s\\n' '{{"number":1,"url":"https://example.test/pr/1","isDraft":false,"baseRefName":"main","headRefName":"feat/step1-task"}}'
    exit 0
  fi
  exit 1
fi
if [ "$1" = "pr" ] && [ "$2" = "create" ]; then
  touch "{state}"
  printf '%s\\n' 'https://example.test/pr/1'
  exit 0
fi
if [ "$1" = "pr" ] && [ "$2" = "edit" ]; then exit 0; fi
if [ "$1" = "pr" ] && [ "$2" = "ready" ]; then exit 0; fi
if [ "$1" = "pr" ] && [ "$2" = "comment" ]; then exit 0; fi
exit 1
""",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])


def prepare_root(root: Path, remote: Path, task_worktree: Path) -> Path:
    artifacts = run_artifacts(root)
    shutil.copy2(Path(__file__).resolve().parents[1] / ".agent-tool-policy.yaml", root / ".agent-tool-policy.yaml")
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "validate_artifacts.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    (scripts / "security_scan.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
    (root / ".agent-policy.yaml").write_text(
        """version: 1
projects:
  agent_workspace:
    publication:
      allowed_branch_prefixes: ["feat/", "fix/", "issue/"]
""",
        encoding="utf-8",
    )
    (root / ".agent-project-profiles.yaml").write_text(
        """version: 1
profiles:
  agent_workspace:
    quality_commands:
      required: ["true"]
    security_commands:
      required: ["true"]
""",
        encoding="utf-8",
    )
    required_roles = [
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
        "publication-prepare",
    ]
    (root / ".agent-routing.yaml").write_text(
        "version: 1\nrequired_before_publication:\n"
        + "".join(f"  - {role}\n" for role in required_roles),
        encoding="utf-8",
    )
    (root / ".agent-repositories.yaml").write_text(
        f"""version: 1
repositories:
  agent_workspace:
    project_profile: agent_workspace
    expected_remotes: ["{remote}"]
    base_branch: main
    allowed_branch_prefixes: ["feat/", "fix/", "issue/"]
    protected_paths: [".env", "auth/**"]
""",
        encoding="utf-8",
    )
    write_json(artifacts / "risk.json", risk_payload())
    write_json(artifacts / "quality.json", quality_payload())
    write_json(artifacts / "verdict.json", verdict_payload())
    for name in ("issue.json", "implementation.json", "test_plan.json", "test_result.json"):
        write_json(artifacts / name, {"status": "completed"})
    (artifacts / "plan.md").write_text("# Plan\n", encoding="utf-8")
    write_json(
        artifacts / "security.json",
        {
            "verdict": "works",
            "expected": [],
            "observed": [],
            "evidence": [],
            "blockers": [],
            "repair_required": False,
            "status": "pass",
            "highest_severity": "none",
            "project_profile": "agent_workspace",
            "findings": [],
            "blocker_ids": [],
            "secret_findings": [],
            "commands_attempted": ["true"],
            "warnings": [],
        },
    )
    write_json(
        artifacts / "review.json",
        {
            "verdict": "works",
            "expected": [],
            "observed": [],
            "evidence": [],
            "blockers": [],
            "repair_required": False,
            "status": "pass",
            "project_profile": "agent_workspace",
            "findings": [],
            "blocker_ids": [],
            "policy_violations": [],
            "known_lesson_conflicts": [],
            "warnings": [],
        },
    )
    write_json(
        artifacts / "project_profile.json",
        {
            "project_profile": "agent_workspace",
            "confidence": "high",
            "reasons": [],
            "matched_markers": [],
            "quality_commands_selected": ["true"],
            "security_commands_selected": ["true"],
            "frontend_evidence_required": False,
            "warnings": [],
        },
    )
    write_json(
        artifacts / "change_set.json",
        {
            "target_repository": ".",
            "project_profile": "agent_workspace",
            "task_id": "step1-task",
            "expected_remote": str(remote),
            "include": ["allowed.txt"],
            "exclude": [],
        },
    )
    write_json(
        artifacts / "publication_payload.json",
        {
            "title": "Step 1 task",
            "body": "Small verified change.",
            "commit_message": "Step 1 task",
            "base_branch": "main",
            "branch": "feat/step1-task",
        },
    )
    write_json(
        artifacts.parent / "workflow.json",
        {
            "run_id": "run-test",
            "execution_status": "running",
            "worktree": str(task_worktree.resolve()),
            "branch": "feat/step1-task",
            "roles": [
                {"role": role, "result": {"status": "completed"}}
                for role in required_roles
            ],
        },
    )
    return artifacts


def test_publication_blocks_when_a_required_gate_is_missing(tmp_path: Path) -> None:
    publisher = publisher_for(tmp_path, runner=FakeRunner())
    run_dir = publisher.artifacts.parent
    (tmp_path / ".agent-routing.yaml").write_text(
        "version: 1\nrequired_before_publication:\n  - planner\n  - test-generator\n",
        encoding="utf-8",
    )
    write_json(
        run_dir / "workflow.json",
        {"roles": [{"role": "planner", "result": {"status": "completed"}}]},
    )
    (publisher.artifacts / "plan.md").write_text("# Plan\n", encoding="utf-8")
    write_json(publisher.artifacts / "project_profile.json", {"project_profile": "agent_workspace"})
    publication = publish_pr.PublicationResult()

    publisher.validate_workflow_gates(publication)

    assert any("test-generator" in error for error in publication.errors)
    assert any("test_plan.json" in error for error in publication.errors)


def test_end_to_end_publication_is_idempotent_and_never_commits_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote, repo, main_sha = init_repo(tmp_path)
    install_fake_gh(tmp_path, monkeypatch)
    root = tmp_path / "control"
    artifacts = prepare_root(root, remote, repo)
    publisher = publisher_for(root)

    first = publisher.publish(repo_override=repo)
    verdict_after_first = (artifacts / "verdict.json").read_bytes()
    second = publisher_for(root).publish(repo_override=repo)

    assert first.execution_status == "completed"
    assert first.worktree == str(repo.resolve())
    assert first.pr_created_or_updated is True
    assert first.pr_url == "https://example.test/pr/1"
    assert git(repo, "rev-parse", "main") == main_sha
    assert git(repo, "rev-parse", "origin/main") == main_sha
    assert second.execution_status == "completed", second.errors
    assert "publication already completed; no-op" in second.warnings
    assert (artifacts / "verdict.json").read_bytes() == verdict_after_first
    assert (artifacts / "publication.json").exists()
    assert not (root / "artifacts").exists()
    assert not (artifacts.parent / "publication.json").exists()
    assert not (root / ".agent-worktrees").exists()
    assert (artifacts.parent / "audit-log.jsonl").exists()


def test_default_and_protected_branch_names_are_blocked(tmp_path: Path) -> None:
    publication = publish_pr.PublicationResult()
    publisher_for(tmp_path).validate_publication_branch(
        "main", "main", publication, ["feat/", "issue/"]
    )
    assert any("protected branch" in error for error in publication.errors)


def test_publication_rejects_a_worktree_unrelated_to_workflow_state(tmp_path: Path) -> None:
    expected = tmp_path / "expected"
    unrelated = tmp_path / "unrelated"
    for repo in (expected, unrelated):
        repo.mkdir()
        git(repo, "init")
        git(repo, "config", "user.name", "Test")
        git(repo, "config", "user.email", "test@example.com")
        (repo / "file.txt").write_text("x\n", encoding="utf-8")
        git(repo, "add", "file.txt")
        git(repo, "commit", "-m", "initial")
        git(repo, "checkout", "-b", "feat/task")
    publisher = publisher_for(tmp_path)
    write_json(
        publisher.artifacts.parent / "workflow.json",
        {"worktree": str(expected), "branch": "feat/task", "roles": []},
    )
    publication = publish_pr.PublicationResult(branch="feat/task")

    publisher.authoritative_task_worktree(unrelated, publication)

    assert any("original task worktree" in error for error in publication.errors)

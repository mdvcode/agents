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
from runtimes.codex_sdk import CodexSdkRuntime  # noqa: E402


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "agent_role_runner.py"
SPEC = importlib.util.spec_from_file_location("agent_role_runner", MODULE_PATH)
assert SPEC is not None
agent_role_runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = agent_role_runner
SPEC.loader.exec_module(agent_role_runner)


def test_workflow_token_ceiling_is_economy_pressure_not_a_blocker() -> None:
    action = agent_role_runner.workflow_token_pressure_action(
        {"budgets": {"max_tokens": 100}, "tokens_used": 125}
    )

    assert action is not None
    assert action["action"] == "economy"
    assert action["exhausted_dimensions"] == ["tokens_used"]


def test_publication_requires_central_registration(monkeypatch: object, tmp_path: Path) -> None:
    monkeypatch.setattr(agent_role_runner, "git_remote", lambda _repository: "local-origin")
    monkeypatch.setattr(agent_role_runner, "find_by_remote", lambda _remote: None)

    assert agent_role_runner.publication_requested("Implement the change", tmp_path) is False


@pytest.mark.parametrize(
    "goal",
    [
        "Исправь проблему без публикации",
        "Implement the change, but do not publish",
        "Make this change local only",
    ],
)
def test_explicit_local_only_goal_disables_publication(
    monkeypatch: object, tmp_path: Path, goal: str
) -> None:
    monkeypatch.setattr(agent_role_runner, "git_remote", lambda _repository: "registered-origin")
    monkeypatch.setattr(agent_role_runner, "find_by_remote", lambda _remote: object())

    assert agent_role_runner.publication_requested(goal, tmp_path) is False


def test_registered_repository_keeps_publication_enabled(
    monkeypatch: object, tmp_path: Path
) -> None:
    monkeypatch.setattr(agent_role_runner, "git_remote", lambda _repository: "registered-origin")
    monkeypatch.setattr(agent_role_runner, "find_by_remote", lambda _remote: object())

    assert agent_role_runner.publication_requested("Implement the change", tmp_path) is True


def test_nextjs_required_typecheck_uses_package_manager_neutral_node_runtime() -> None:
    commands = agent_role_runner.profile_required_commands("nextjs_web", "quality_commands")

    assert "node node_modules/typescript/lib/tsc.js --noEmit --incremental false" in commands
    assert not any(command.startswith("bun ") for command in commands)


def test_attention_stops_a_question_that_was_already_answered() -> None:
    state = {
        "attention_history": [
            {
                "role": "planner",
                "summary": "Which export format should be used?",
                "resolution": "answer_recorded",
            }
        ]
    }
    question = {
        "id": "export_format",
        "options": [
            {
                "label": "CSV",
                "description": "Simple tabular export.",
                "value": "csv",
                "recommended": False,
            },
            {
                "label": "JSON",
                "description": "Preserves nested data.",
                "value": "json",
                "recommended": True,
            },
        ],
        "allow_custom": True,
    }
    legacy_fingerprint = agent_role_runner.attention_fingerprint(
        role="planner",
        summary="Which export format should be used?",
    )
    state["attention_history"][0]["fingerprint"] = legacy_fingerprint

    repeated = agent_role_runner.set_attention(
        state,
        summary="Which export format should be used?",
        details=["Choose CSV or JSON."],
        role="planner",
        action="answer",
        question=question,
        stop_if_previously_answered=True,
    )

    assert repeated is True
    assert state["attention"]["action"] == "fix_then_retry"
    assert state["attention"]["repeated_question"] is True
    assert "repeated" in state["attention"]["summary"].lower()


def test_question_options_are_bounded_and_recommended_option_is_first() -> None:
    state: dict[str, object] = {}

    repeated = agent_role_runner.set_attention(
        state,
        summary="Choose an environment.",
        details=[],
        role="planner",
        action="answer",
        question={
            "id": "Environment Choice",
            "options": [
                {
                    "label": "Local",
                    "value": "local",
                    "recommended": False,
                    "requires_input": False,
                },
                {
                    "label": "Staging",
                    "value": "staging",
                    "recommended": True,
                    "requires_input": True,
                },
                {"label": "Preview", "value": "preview", "recommended": False},
                {"label": "Production", "value": "production", "recommended": False},
            ],
            "allow_custom": True,
        },
    )

    assert repeated is False
    question = state["attention"]["question"]  # type: ignore[index]
    assert question["id"] == "environment_choice"
    assert [option["value"] for option in question["options"]] == [
        "staging",
        "local",
        "preview",
    ]
    assert question["options"][0]["recommended"] is True
    assert question["options"][0]["requires_input"] is True
    assert question["options"][1]["requires_input"] is False
    assert question["options"][2]["requires_input"] is False


def test_semantically_equivalent_missing_requirement_is_stopped() -> None:
    state: dict[str, object] = {}
    first_question = {
        "id": "database_environment",
        "requirement": "database environment",
        "options": [
            {"label": "Local", "value": "local", "recommended": True},
            {"label": "Staging", "value": "staging", "recommended": False},
        ],
    }
    second_question = {
        "id": "db_target",
        "requirement": "DB environment",
        "options": [
            {"label": "Local", "value": "local", "recommended": True},
            {"label": "Staging", "value": "staging", "recommended": False},
        ],
    }

    assert agent_role_runner.set_attention(
        state,
        summary="Which database environment should be used?",
        details=[],
        role="planner",
        action="answer",
        question=first_question,
        stop_if_previously_answered=True,
    ) is False
    assert agent_role_runner.set_attention(
        state,
        summary="Select the DB target for this run.",
        details=[],
        role="planner",
        action="answer",
        question=second_question,
        stop_if_previously_answered=True,
    ) is True

    assert state["attention"]["action"] == "fix_then_retry"  # type: ignore[index]
    assert state["attention"]["repeated_requirement"] is True  # type: ignore[index]
    assert len(state["missing_requirement_requests"]) == 1  # type: ignore[arg-type]


def test_answer_continuation_is_not_classified_as_prior_role_failure() -> None:
    state = {
        "roles": [
            {
                "role": "planner",
                "result": {"status": "awaiting_approval"},
            }
        ]
    }

    assert agent_role_runner.prior_role_failed(state, "planner") is False
    state["roles"].append(
        {"role": "planner", "result": {"status": "failed", "_failure": {}}}
    )
    assert agent_role_runner.prior_role_failed(state, "planner") is True


def test_technical_failures_are_not_classified_as_answerable_questions() -> None:
    assert agent_role_runner.role_attention_action({"status": "blocked"}) == "fix_then_retry"
    assert agent_role_runner.role_attention_action({"status": "failed"}) == "fix_then_retry"
    assert agent_role_runner.role_attention_action({"status": "awaiting_approval"}) == "answer"


def test_blocked_role_with_structured_question_remains_answerable() -> None:
    result = {
        "status": "blocked",
        "question": {
            "id": "backend_signal_format",
            "requirement": "Which backend signal format should be used?",
            "options": [
                {
                    "label": "Provide backend ticket",
                    "value": "provide_backend_ticket",
                    "recommended": True,
                },
                {
                    "label": "Provide signal format",
                    "value": "provide_signal_spec",
                    "recommended": False,
                },
            ],
            "allow_custom": True,
        },
    }

    assert agent_role_runner.role_attention_action(result) == "answer"


def test_blocked_role_with_malformed_question_requires_a_technical_fix() -> None:
    result = {
        "status": "blocked",
        "question": {"id": "backend_signal_format", "options": []},
    }

    assert agent_role_runner.role_attention_action(result) == "fix_then_retry"


def fake_adapter_script(path: Path) -> str:
    (path.parent / "Makefile").write_text(
        "check:\n\t@true\nsecurity:\n\t@true\n", encoding="utf-8"
    )
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


@pytest.mark.parametrize(
    ("requested", "goal", "expected"),
    [
        ("auto", "Make the service images brighter with CSS", "fast"),
        ("auto", "Change authentication permissions", "full"),
        ("auto", "Implement a small local feature", "fast"),
        ("auto", "", "full"),
        ("fast", "Change authentication permissions", "fast"),
        ("adaptive", "Fix a typo", "adaptive"),
        ("full", "Fix a typo", "full"),
        ("goal", "Complete a checkpointed multi-hour objective", "goal"),
    ],
)
def test_execution_mode_selection(requested: str, goal: str, expected: str) -> None:
    assert agent_role_runner.select_execution_mode(requested, goal) == expected


def test_repository_execution_modes_have_distinct_session_budgets() -> None:
    adaptive = agent_role_runner.workflow_budgets("full_agent_workflow", "adaptive")
    fast = agent_role_runner.workflow_budgets("full_agent_workflow", "fast")
    full = agent_role_runner.workflow_budgets("full_agent_workflow", "full")
    goal = agent_role_runner.workflow_budgets("full_agent_workflow", "goal")

    assert fast["max_duration_seconds"] == 900
    assert adaptive["max_duration_seconds"] == 1800
    assert full["max_duration_seconds"] == 3600
    assert goal["max_duration_seconds"] == 14_400
    assert fast["max_roles"] < full["max_roles"] < goal["max_roles"]


def test_adaptive_plan_is_compiled_deterministically_and_parallelism_is_read_only() -> None:
    plan = agent_role_runner.compile_adaptive_execution_plan(
        task_id="fix-status",
        goal="Fix the status regression in ai_harness/cli.py",
        project_profile="agent_workspace",
        requested_paths=["ai_harness/cli.py"],
    )

    assert plan["mode"] == "adaptive"
    assert "quality-runner" in plan["required_roles"]
    assert "security-agent" in plan["required_roles"]
    assert all(
        node["read_only"] is True
        for group in plan["parallel_groups"]
        for node in plan["nodes"]
        if node["id"] in group
    )


def test_completed_role_result_is_reused_after_post_role_approval() -> None:
    completed = {"status": "completed", "summary": "review passed"}
    state = {
        "roles": [
            {"role": "reviewer", "result": completed},
            {"role": "approval-gate", "result": {"status": "awaiting_approval"}},
        ]
    }

    assert agent_role_runner.completed_role_result(state, "reviewer") is completed


def test_missing_distinct_image_capability_fails_before_runtime(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "plan.md").write_text(
        "Add six distinct new image assets for each category.\n", encoding="utf-8"
    )

    reason = agent_role_runner.missing_image_capability(
        "Добавь к каждой категории картинки, для каждой категории своя", artifacts
    )

    assert "no image-generation capability" in reason


def test_image_capability_terms_in_unrelated_requirements_do_not_block(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "plan.md").write_text(
        "Create only the project scaffold.\nUse provided assets without modifying them.\n",
        encoding="utf-8",
    )

    reason = agent_role_runner.missing_image_capability(
        "Используй предоставленные изображения.\nСоздай отдельный плагин.", artifacts
    )

    assert reason == ""


def test_supplied_images_without_generation_do_not_block(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "plan.md").write_text(
        "Use the six supplied images, with a distinct image for each category.\n",
        encoding="utf-8",
    )

    reason = agent_role_runner.missing_image_capability(
        "Do not generate new images; use the provided files.", artifacts
    )

    assert reason == ""


def test_answered_image_capability_requirement_allows_resume(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "plan.md").write_text(
        "Add six distinct new image assets for each category.\n", encoding="utf-8"
    )
    fingerprint = "sha256:image-capability"
    (tmp_path / "workflow.json").write_text(
        json.dumps(
            {
                "attention_history": [
                    {
                        "role": "implementation-agent",
                        "resolution": "answer_recorded",
                        "fingerprint": fingerprint,
                        "requirement": {
                            "requirement_id": "capability_implementation_unavailable"
                        },
                        "details": [
                            "The plan requires images, but the role has no image-generation capability."
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "human-input.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "requirement_id": "capability_implementation_unavailable",
                        "question_fingerprint": fingerprint,
                        "response": "Use the supplied image assets and continue.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    reason = agent_role_runner.missing_image_capability(
        "Добавь к каждой категории картинки, для каждой категории своя", artifacts
    )

    assert reason == ""


@pytest.mark.parametrize(
    ("fingerprint", "response"),
    [("sha256:other-question", "Use supplied images"), ("sha256:image-capability", "")],
)
def test_unmatched_or_empty_image_capability_answer_does_not_bypass(
    tmp_path: Path, fingerprint: str, response: str
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "plan.md").write_text(
        "Add six distinct new image assets for each category.\n", encoding="utf-8"
    )
    (tmp_path / "workflow.json").write_text(
        json.dumps(
            {
                "attention_history": [
                    {
                        "role": "implementation-agent",
                        "resolution": "answer_recorded",
                        "fingerprint": "sha256:image-capability",
                        "requirement": {
                            "requirement_id": "capability_implementation_unavailable"
                        },
                        "details": ["No image-generation capability is available."],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "human-input.json").write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "requirement_id": "capability_implementation_unavailable",
                        "question_fingerprint": fingerprint,
                        "response": response,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    reason = agent_role_runner.missing_image_capability("Generate six new images.", artifacts)

    assert "no image-generation capability" in reason


def test_explicit_generation_from_supplied_reference_still_blocks(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "plan.md").write_text(
        "Generate six new images using the supplied logo as a reference.\n", encoding="utf-8"
    )

    reason = agent_role_runner.missing_image_capability("Create the category art.", artifacts)

    assert "no image-generation capability" in reason


def test_fast_workflow_uses_only_implementation_model_for_non_code_change(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    monkeypatch.setattr(agent_role_runner, "RUNS", tmp_path / ".agent-runs")
    (tmp_path / "Makefile").write_text("check:\n\t@true\nsecurity:\n\t@true\n", encoding="utf-8")
    command = fake_adapter_script(tmp_path / "fake_adapter.py")

    state = agent_role_runner.run_roles(
        run_id="fast-role-bound",
        repository=tmp_path,
        adapter_command=command,
        dry_run=True,
        mode="auto",
        goal="Fix CSS color",
    )

    model_roles = [item["role"] for item in state["roles"] if item["llm_invoked"]]
    assert model_roles == ["implementation-agent"]
    assert not {"planner", "risk-classifier", "test-generator"} & {
        item["role"] for item in state["roles"]
    }
    assert state["budgets"]["max_duration_seconds"] == 900
    assert state["effective_mode"] == "fast"


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

    monkeypatch.setattr(CodexSdkRuntime, "preflight", fake_preflight)

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
    assert state["runtime"]["provider"] == "codex-sdk"


def test_current_branch_mode_uses_source_checkout_and_revalidates_branch(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "switch", "-c", "feature/current"], cwd=tmp_path, check=True, capture_output=True)
    runs = tmp_path.parent / "current-branch-runs"
    monkeypatch.setattr(agent_role_runner, "RUNS", runs)

    worktree, branch, base, errors = agent_role_runner.prepare_worktree(
        tmp_path,
        "current-task",
        "",
        "run-current",
        "feature/current",
        "main",
        True,
        True,
    )

    assert errors == []
    assert worktree == tmp_path.resolve()
    assert branch == "feature/current"
    assert base == "main"
    assert not (tmp_path / ".agent-worktrees").exists()
    recorded = json.loads((runs / "run-current" / "worktree.json").read_text(encoding="utf-8"))
    assert recorded["workspace_mode"] == "checkout"
    assert recorded["checkout_path"] == str(tmp_path.resolve())
    assert recorded["task_branch"] == "feature/current"
    assert recorded["branch_owner_run_id"] == "run-current"
    assert recorded["worktree"] == str(tmp_path.resolve())

    subprocess.run(["git", "switch", "main"], cwd=tmp_path, check=True, capture_output=True)
    _, _, _, changed_errors = agent_role_runner.prepare_worktree(
        tmp_path,
        "current-task",
        "",
        "run-changed",
        "feature/current",
        "main",
        True,
        True,
    )
    assert any("current branch changed before execution" in error for error in changed_errors)

    subprocess.run(["git", "switch", "feature/current"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "README.md").write_text("changed after intake\n", encoding="utf-8")
    _, _, _, dirty_errors = agent_role_runner.prepare_worktree(
        tmp_path,
        "current-task",
        "",
        "run-dirty",
        "feature/current",
        "main",
        True,
        True,
    )
    assert any("uncommitted changes" in error for error in dirty_errors)


def test_agent_role_runner_invokes_adapter_for_core_roles(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(agent_role_runner, "RUNS", tmp_path / ".agent-runs")
    command = fake_adapter_script(tmp_path / "fake_adapter.py")

    state = agent_role_runner.run_roles(
        run_id="run-2",
        repository=tmp_path,
        adapter_command=command,
        dry_run=True,
    )

    assert state["execution_status"] == "blocked"
    assert [item["role"] for item in state["roles"]][:9] == [
        "issue-intake",
        "context-compiler",
        "planner",
        "risk-classifier",
        "implementation-agent",
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


def test_technical_publication_failure_does_not_create_approval_gate(
    tmp_path: Path, monkeypatch: object
) -> None:
    runs = tmp_path / ".agent-runs"
    monkeypatch.setattr(agent_role_runner, "RUNS", runs)
    command = fake_adapter_script(tmp_path / "fake_adapter.py")
    state = agent_role_runner.run_roles(
        run_id="resume-checkpoint",
        repository=tmp_path,
        adapter_command=command,
        dry_run=True,
    )
    run_dir = runs / "resume-checkpoint"

    assert state["execution_status"] == "blocked"
    assert state["current_role"] == "publication"
    assert state["attention"]["action"] == "fix_then_retry"
    assert state["attention"]["summary"] == "Publication executor blocked or failed."
    assert not (run_dir / "artifacts" / "approval.json").exists()


def test_resumed_run_stops_instead_of_reopening_the_same_question(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    runs = tmp_path / ".agent-runs"
    monkeypatch.setattr(agent_role_runner, "RUNS", runs)
    command = fake_adapter_script(tmp_path / "fake_adapter.py")

    def ask_same_question(*args: object, **kwargs: object) -> dict[str, object]:
        artifacts = Path(str(kwargs["artifacts"]))
        (artifacts / "plan.md").write_text("# Plan\n", encoding="utf-8")
        return {
            "status": "awaiting_approval",
            "next_action": "awaiting_approval",
            "summary": "Which export format should be used?",
            "artifacts_created": ["plan.md"],
            "blockers": ["Choose CSV or JSON."],
            "warnings": [],
            "tokens_used": 1,
            "question": {
                "id": "export_format",
                "options": [
                    {
                        "label": "JSON",
                        "description": "Preserves nested data.",
                        "value": "json",
                        "recommended": True,
                    },
                    {
                        "label": "CSV",
                        "description": "Simple tabular export.",
                        "value": "csv",
                        "recommended": False,
                    },
                ],
                "allow_custom": True,
            },
        }

    monkeypatch.setattr(agent_role_runner, "execute_runtime_observed", ask_same_question)
    first = agent_role_runner.run_roles(
        run_id="repeat-question",
        repository=tmp_path,
        adapter_command=command,
        dry_run=True,
    )
    run_dir = runs / "repeat-question"
    approval = approve_run(run_dir, actor="user")
    workflow_path = run_dir / "workflow.json"
    workflow = json.loads(workflow_path.read_text(encoding="utf-8"))
    attention = workflow.pop("attention")
    workflow["attention_history"] = [
        {**attention, "required": False, "resolution": "answer_recorded"}
    ]
    workflow["blockers"] = []
    workflow_path.write_text(json.dumps(workflow), encoding="utf-8")
    checkpoint = agent_role_runner.read_checkpoint(run_dir, "planner")
    assert checkpoint is not None
    agent_role_runner.write_checkpoint(
        run_dir,
        agent_role_runner.RoleCheckpoint(
            run_id=checkpoint.run_id,
            role=checkpoint.role,
            state="role_pending",
            attempt=checkpoint.attempt,
            worktree=checkpoint.worktree,
            input_fingerprint=checkpoint.input_fingerprint,
        ),
    )
    prepare_resume(run_dir)

    resumed = agent_role_runner.run_roles(run_id="repeat-question", resume=True, dry_run=True)

    assert first["execution_status"] == "awaiting_approval"
    assert resumed["execution_status"] == "blocked"
    assert resumed["attention"]["repeated_question"] is True
    assert resumed["attention"]["action"] == "fix_then_retry"
    current_approval = json.loads(
        (run_dir / "artifacts" / "approval.json").read_text(encoding="utf-8")
    )
    assert current_approval["approval_id"] == approval["approval_id"]
    assert current_approval["status"] == "consumed"


def test_approval_request_failure_replaces_stale_question_with_actionable_attention(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    runs = tmp_path / ".agent-runs"
    monkeypatch.setattr(agent_role_runner, "RUNS", runs)
    command = fake_adapter_script(tmp_path / "fake_adapter.py")

    def reject_approval(*args: object, **kwargs: object) -> None:
        raise agent_role_runner.ApprovalError("approval store unavailable")

    monkeypatch.setattr(agent_role_runner, "request_approval", reject_approval)
    monkeypatch.setattr(
        agent_role_runner,
        "decide_next_role",
        lambda **_kwargs: {
            "next_role": "approval-gate",
            "reason": "Explicit approval required.",
            "stop": True,
            "publication_allowed": False,
            "loop": None,
            "warnings": [],
        },
    )

    state = agent_role_runner.run_roles(
        run_id="approval-request-failure",
        repository=tmp_path,
        adapter_command=command,
        dry_run=True,
    )

    assert state["execution_status"] == "blocked"
    assert state["attention"]["summary"] == "The approval request could not be created."
    assert state["attention"]["action"] == "fix_then_retry"
    assert state["attention"]["details"] == [
        "approval request failed: approval store unavailable"
    ]


def test_unfinished_run_cannot_be_restarted_without_resume(tmp_path: Path, monkeypatch: object) -> None:
    runs = tmp_path / ".agent-runs"
    monkeypatch.setattr(agent_role_runner, "RUNS", runs)
    monkeypatch.setattr(
        agent_role_runner,
        "decide_next_role",
        lambda **_kwargs: {
            "next_role": "approval-gate",
            "reason": "Explicit approval required.",
            "stop": True,
            "publication_allowed": False,
            "loop": None,
            "warnings": [],
        },
    )
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
    assert state["execution_status"] == "blocked"
    planner = next(item for item in state["roles"] if item["role"] == "planner")
    assert planner["result"]["summary"] == "Role artifact ownership validation failed."
    assert not (tmp_path / ".agent-runs" / "ownership-run" / "artifacts" / "verdict.json").exists()
    errors_path = tmp_path / ".agent-runs" / "ownership-run" / "errors.jsonl"
    errors = errors_path.read_text(encoding="utf-8")
    assert "ROLE_NOT_COMPLETED" in errors
    assert "ROUTER_BLOCKED" in errors


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


def test_dependency_change_fails_closed_when_vulnerability_scanner_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    monkeypatch.setattr(agent_role_runner, "profile_required_commands", lambda *_args: [])
    monkeypatch.setattr(agent_role_runner.shutil, "which", lambda _name: None)

    result = agent_role_runner.run_deterministic_security(
        project_profile="django",
        repository=tmp_path,
        artifacts_dir=artifacts,
        timeout_seconds=30,
        required_checks={"dependency_audit"},
    )
    evidence = json.loads((artifacts / "security.json").read_text(encoding="utf-8"))

    assert result["status"] == "awaiting_approval"
    assert evidence["verdict"] == "unavailable"
    assert evidence["evidence"][-1]["command"] == "dependency vulnerability scanner"

#!/usr/bin/env python3
"""Collect a local Codex SDK single-invocation baseline, never a desktop-chat benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time

import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for import_root in (ROOT, ROOT / "scripts"):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from ai_harness.context.content_guard import require_safe  # noqa: E402
from ai_harness.context.payload import write_private_json, write_private_text  # noqa: E402
from ai_harness.model_policy import load_execution_profiles  # noqa: E402
from ai_harness.paths import harness_home  # noqa: E402
from ai_harness.planning import TaskAnalyzer  # noqa: E402
from ai_harness.project import ProjectConfig, load_project_config, project_is_trusted, trust_key  # noqa: E402
from ai_harness.result_acceptance import BASELINE_FILES, MAX_GIT_BYTES, _digest, _git, _read_bytes, _snapshot, baseline_receipt, baseline_tokens  # noqa: E402
from agent_role_runner import (  # noqa: E402
    build_role_request, preflight_role_execution, role_filesystem_access, role_tools,
    run_context_compiler, run_deterministic_orchestrator, run_deterministic_quality, run_deterministic_review,
    run_deterministic_security, validate_manifest, validate_role_artifacts, validate_role_result,
)
from context_compiler import create_context_manifest  # noqa: E402
from run_state import RunLayout, file_contents_snapshot, file_snapshot, ownership_errors, restore_foreign_artifacts  # noqa: E402
from publish_pr import protected_path_blockers, read_yaml  # noqa: E402
from repository_registry import load_registry  # noqa: E402
from runtimes import create_runtime  # noqa: E402
from runtimes.base import Runtime  # noqa: E402
from workflow_router import FAST_SENSITIVE_PARTS, fast_path_blockers  # noqa: E402


class BaselineError(ValueError):
    """The disposable baseline cannot run within its declared boundaries."""



def _enforce_path_policy(paths: list[str], profile: str) -> None:
    """Read the active control-plane policy; local project identity grants no rights."""
    home = harness_home()
    try:
        policy = read_yaml(home / ".agent-policy.yaml")
        registry_path = home / ".agent-repositories.yaml"
        if not registry_path.is_file():
            raise BaselineError("The active repository policy is unavailable")
        records = load_registry(registry_path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise BaselineError("Cannot read the active control-plane path policy") from exc
    risks = policy.get("risk_classes")
    low = risks.get("low") if isinstance(risks, dict) else None
    if policy.get("version") != 1 or not isinstance(low, dict) or low.get("patch") is not True or low.get("require_human_approval") is not False:
        raise BaselineError("The active policy does not authorize LOW-risk local patches")
    projects = policy.get("projects")
    if not isinstance(projects, dict):
        raise BaselineError("Active project policy is malformed")
    project = projects.get(profile, {})
    patterns = project.get("protected_paths", []) if isinstance(project, dict) else None
    if not isinstance(patterns, list) or not all(isinstance(value, str) and value for value in patterns):
        raise BaselineError("Active protected-path policy is malformed")
    patterns = list(patterns)
    for record in records.values():
        if record.project_profile == profile:
            patterns.extend(record.protected_paths)
    if protected_path_blockers({"include": paths}, policy, profile, patterns):
        raise BaselineError("The active policy protects a requested or changed baseline path")

def _validate_target(repository: Path, base_sha: str, goal: str, allowed_paths: list[str], confirmed: bool) -> ProjectConfig:
    if not confirmed:
        raise BaselineError("Explicit --confirm-disposable is required")
    config = load_project_config(repository)
    if not project_is_trusted(config):
        raise BaselineError("Initialize and trust this disposable repository with agent init first")
    if _git(repository, "remote").strip():
        raise BaselineError("The first baseline collector supports disposable repositories without remotes only")
    branch = _git(repository, "symbolic-ref", "--quiet", "--short", "HEAD").decode().strip()
    if not branch.startswith("baseline/") or branch == config.base_branch:
        raise BaselineError("Select a dedicated non-default baseline/* branch first")
    if not re.fullmatch(r"[0-9a-f]{40,64}", base_sha) or _git(repository, "rev-parse", "HEAD").decode().strip() != base_sha:
        raise BaselineError("The disposable checkout must start at the exact supplied base commit")
    if _git(repository, "status", "--porcelain", "--untracked-files=normal"):
        raise BaselineError("The disposable checkout must be clean")
    if not goal.strip() or len(goal) > 10000 or not 1 <= len(allowed_paths) <= 5:
        raise BaselineError("Provide a bounded goal and one to five explicit --allow-path files")
    require_safe(goal, "Baseline goal")
    _enforce_path_policy(allowed_paths, config.profile)
    for value in allowed_paths:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or not value or any(part.startswith(".") for part in path.parts):
            raise BaselineError("Allowed files must be repository-relative and exclude control-plane paths")
        normalized = value.lower().replace("\\", "/")
        if path.name in {"AGENTS.md", "Makefile", "GNUmakefile", "makefile", "pyproject.toml", "package.json", "package-lock.json", "bun.lock", "bun.lockb"} or path.name.startswith("requirements"):
            raise BaselineError("Execution configuration and dependency changes require the governed workflow")
        if any(part.startswith(".env") for part in path.parts) or normalized.endswith((".pem", ".key")) or any(part in normalized for part in FAST_SENSITIVE_PARTS | {"secret", "settings_prod.py", "production.py"}):
            raise BaselineError("Protected or sensitive paths are not eligible for a baseline")
        if (repository / path).is_dir():
            raise BaselineError("Allowed paths must identify individual files")
        if not (repository / path).resolve().is_relative_to(repository):
            raise BaselineError("An allowed file points outside the disposable repository")
    analysis = TaskAnalyzer().analyze(goal, repository_profile=config.profile, requested_paths=allowed_paths)
    if analysis.risk != "low" or any((analysis.requires_architecture_review, analysis.requires_security_review, analysis.requires_frontend_verification, analysis.requires_semantic_review)):
        raise BaselineError("Only narrow LOW-risk tasks are eligible for the one-pass baseline")
    return config



def _verify_output_boundary(repository: Path, base_sha: str, allowed_paths: list[str], state: dict[str, Any], artifacts: Path) -> None:
    if _git(repository, "rev-parse", "HEAD").decode().strip() != base_sha:
        raise BaselineError("The implementation changed Git history; this is not a local baseline result")
    changed = _git(repository, "diff", "--name-only", "-z", "HEAD", "--")
    untracked = _git(repository, "ls-files", "--others", "--exclude-standard", "-z")
    changed_paths = {os.fsdecode(value) for value in (changed + untracked).split(b"\0") if value}
    _enforce_path_policy(sorted(changed_paths), str(state["project_profile"]))
    if changed_paths - set(allowed_paths) or fast_path_blockers(state, artifacts):
        raise BaselineError("The result exceeded the allowed paths or LOW-risk fast limits")
    new_lines = 0
    for value in untracked.split(b"\0"):
        if value:
            path = repository / os.fsdecode(value)
            if not path.resolve().is_relative_to(repository) or path.is_symlink():
                raise BaselineError("New baseline files must remain inside the disposable repository")
            new_lines += len(_read_bytes(path, MAX_GIT_BYTES).splitlines())
    tracked_lines = 0
    for row in _git(repository, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--numstat", "-z", "HEAD", "--").split(b"\0"):
        columns = row.split(b"\t", 2)
        if len(columns) == 3:
            if not all(value.isdigit() for value in columns[:2]):
                raise BaselineError("Binary changes require the governed workflow")
            tracked_lines += int(columns[0]) + int(columns[1])
    if tracked_lines + new_lines > 200:
        raise BaselineError("Baseline changes exceed the 200-line limit")

def collect_baseline(
    *, repository: Path, base_sha: str, goal: str, allowed_paths: list[str],
    run_id: str, confirmed_disposable: bool, execution_profile: str = "balanced",
    timeout_seconds: int = 180, token_budget: int = 30000,
    runs_dir: Path | None = None, runtime: Runtime | None = None,
) -> dict[str, Any]:
    """One Runtime execution plus existing deterministic checks; no queue or publication."""

    repository = repository.resolve()
    config = _validate_target(repository, base_sha, goal, allowed_paths, confirmed_disposable)
    profiles = load_execution_profiles()
    if execution_profile not in profiles or not 1 <= timeout_seconds <= 900 or not 1 <= token_budget <= 300000:
        raise BaselineError("Invalid configured execution profile or baseline bounds")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}", run_id):
        raise BaselineError("Invalid baseline run id")
    parent = runs_dir if runs_dir is not None else harness_home() / ".agent-runs"
    if parent.is_symlink():
        raise BaselineError("Run storage must not be a symbolic link")
    parent.mkdir(parents=True, exist_ok=True)
    run_dir = parent / run_id
    run_dir.mkdir(mode=0o700, exist_ok=False)
    layout = RunLayout.create(parent, run_id)
    selected_runtime = runtime or create_runtime(provider="codex-sdk", timeout_seconds=timeout_seconds, raw_output_dir=layout.raw_events)
    descriptor = selected_runtime.descriptor
    if descriptor.provider != "codex-sdk" or descriptor.production is not True or descriptor.api_required is not False:
        raise BaselineError("Baseline collection requires the configured production subscription SDK Runtime")
    branch = _git(repository, "symbolic-ref", "--quiet", "--short", "HEAD").decode().strip()
    state = {
        "run_id": run_id, "task_id": run_id, "workflow": "direct_sdk_baseline", "effective_mode": "direct_codex",
        "execution_status": "running", "current_role": "implementation-agent", "goal": goal,
        "repository": str(repository), "checkout_path": str(repository), "worktree": str(repository),
        "task_branch": branch, "base_sha": base_sha, "project": config.project_id,
        "project_profile": config.profile, "runtime": descriptor.as_json(), "loops": {}, "attention_history": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "budgets": {"model_timeout_seconds": timeout_seconds, "check_stage_timeout_seconds": 180, "max_tokens": token_budget},
    }
    started = time.monotonic()
    write_private_json(layout.workflow, state)
    try:
        preflight = selected_runtime.preflight(worktree=repository, timeout_seconds=min(timeout_seconds, 60))
        write_private_json(layout.root / "runtime-preflight.json", preflight)
        if preflight.get("execution_status") != "completed":
            raise BaselineError("Runtime preflight did not pass")
        role = "implementation-agent"
        scoped_goal = goal + "\nLocal only. Do not publish, commit, create children, or modify files outside: " + ", ".join(allowed_paths)
        run_context_compiler(
            run_id=run_id, goal=scoped_goal, project=config.project_id, project_key=trust_key(repository),
            worktree=repository, artifacts_dir=layout.artifacts, context_dir=layout.context,
            project_profile=config.profile, token_budget=token_budget, execution_mode="fast",
        )
        manifest = create_context_manifest(
            run_id=run_id, role=role, goal=scoped_goal, repository=repository, artifacts_dir=layout.artifacts,
            context_dir=layout.context, project=config.project_id, project_key=trust_key(repository),
            project_profile=config.profile, token_budget=token_budget, allowed_tools=role_tools(role),
            previous_roles=[], filesystem_access=role_filesystem_access(role), runtime="codex-sdk",
        )
        errors = validate_manifest(manifest, role)
        if errors or preflight_role_execution(role=role, repository=repository, artifacts_dir=layout.artifacts, project_profile=config.profile, dry_run=False) is not None:
            raise BaselineError("Implementation context or governed tool preflight did not pass")
        settings = {
            **profiles[execution_profile], "execution_profile": execution_profile,
            "profile_reason": "Explicit fixed baseline profile", "escalation_level": 0,
        }
        request = build_role_request(
            run_id=run_id, role=role, goal=scoped_goal, repository=repository, artifacts_dir=layout.artifacts,
            context_manifest=manifest, token_budget=token_budget, timeout_seconds=timeout_seconds,
            project_profile=config.profile, execution_settings=settings,
        )
        write_private_json(layout.requests / f"{role}.json", request)
        before = file_snapshot(layout.artifacts)
        before_contents = file_contents_snapshot(layout.artifacts)
        result = selected_runtime.execute(role=role, context=manifest, task=request, worktree=repository, artifacts=layout.artifacts)
        write_private_json(layout.role_results / f"{role}.json", result)
        errors = validate_role_result(result, role)
        foreign = ownership_errors(role=role, allowed_artifacts=["implementation.json"], before=before, after=file_snapshot(layout.artifacts))
        if foreign:
            restore_foreign_artifacts(directory=layout.artifacts, allowed_artifacts=["implementation.json"], before=before_contents)
            errors.extend(foreign)
        errors.extend(validate_role_artifacts(
            role=role, result=result, artifacts_dir=layout.artifacts, worktree=repository,
            source_repository=repository, source_snapshot_before="", create_task_worktree=False,
        ))
        state["roles"] = [{"role": role, "llm_invoked": True, "result": result, "execution_profile": settings}]
        if result.get("status") != "completed" or result.get("child_tasks") or errors:
            raise BaselineError("The single implementation invocation did not produce valid bounded output")
        _verify_output_boundary(repository, base_sha, allowed_paths, state, layout.artifacts)
        run_deterministic_quality(goal=goal, project_profile=config.profile, repository=repository, artifacts_dir=layout.artifacts, timeout_seconds=180)
        run_deterministic_security(project_profile=config.profile, repository=repository, artifacts_dir=layout.artifacts, timeout_seconds=180, required_checks={"secret_scan"})
        review = run_deterministic_review(project_profile=config.profile, repository=repository, artifacts_dir=layout.artifacts)
        review_path = layout.artifacts / "review.json"
        review_artifact = json.loads(review_path.read_text())
        review_artifact["verification_kind"] = "deterministic_baseline"
        review_artifact["warnings"] = ["No independent model reviewer ran in this one-pass baseline."]
        write_private_json(review_path, review_artifact)
        if review.get("status") != "completed":
            raise BaselineError("Required deterministic baseline checks did not pass; no automatic repair was attempted")
        _verify_output_boundary(repository, base_sha, allowed_paths, state, layout.artifacts)
        state.update(execution_status="completed", current_role="baseline-complete", elapsed_seconds=round(time.monotonic() - started, 3))
        state["tokens_used"] = baseline_tokens(layout.root)
        write_private_json(layout.workflow, state)
        write_private_json(layout.metrics, {"tokens_used": state.get("tokens_used"), "repair_attempts_per_task": 0})
        run_deterministic_orchestrator(goal=scoped_goal, project_profile=config.profile, repository=repository, artifacts_dir=layout.artifacts)
        verdict_path = layout.artifacts / "verdict.json"
        verdict = json.loads(verdict_path.read_text())
        verdict.update(decision="local_complete", baseline_only=True, approval_required_before_publish=True)
        verdict["warnings"].append("Human acceptance and independent model review are not established.")
        write_private_json(verdict_path, verdict)
        write_private_text(layout.raw_events / "approvals.jsonl", "")
        snapshot, _ = _snapshot(layout.root)
        fingerprints = {}
        for name in BASELINE_FILES:
            path = layout.root / name
            if not path.is_file() or path.is_symlink():
                raise BaselineError("Authoritative Runtime evidence is missing; no baseline receipt can be issued")
            fingerprints[name] = hashlib.sha256(_read_bytes(path, MAX_GIT_BYTES)).hexdigest()
        receipt = {
            "schema_version": 1, "kind": "direct_sdk_baseline", "collector": "run_direct_baseline_v1",
            "run_id": run_id, "provider": "codex-sdk", "runtime_invocations": 1,
            "label": "Codex SDK · один проход", "result_fingerprint": snapshot["result_fingerprint"],
            "snapshot": snapshot, "snapshot_fingerprint": _digest(snapshot),
            "file_sha256": fingerprints, "settings": settings, "allowed_paths": allowed_paths,
            "limitations": ["Not a measurement of the desktop chat", "SDK structured-output recovery may add internal turns", "No model reviewer or orchestration repair loop"],
        }
        write_private_json(layout.root / "direct-baseline.json", receipt)
        validation = baseline_receipt(layout.root, {**snapshot, "eligible": True})
        if not validation["valid"]:
            raise BaselineError(validation["reason"])
        return {"run_id": run_id, "execution_status": "completed", "label": receipt["label"], "acceptance": "not_reviewed"}
    except (ValueError, OSError) as exc:
        state.update(execution_status="blocked", elapsed_seconds=round(time.monotonic() - started, 3), blockers=[str(exc)])
        write_private_json(layout.workflow, state)
        raise BaselineError(str(exc)) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--allow-path", action="append", required=True)
    parser.add_argument("--confirm-disposable", action="store_true")
    parser.add_argument("--execution-profile", choices=("balanced", "complex", "economy"), default="balanced")
    parser.add_argument("--timeout-seconds", type=int, default=180)
    parser.add_argument("--token-budget", type=int, default=30000)
    args = parser.parse_args()
    try:
        result = collect_baseline(repository=args.repo, base_sha=args.base_sha, goal=args.goal, allowed_paths=args.allow_path,
                                  run_id=args.run_id, confirmed_disposable=args.confirm_disposable,
                                  execution_profile=args.execution_profile, timeout_seconds=args.timeout_seconds, token_budget=args.token_budget)
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

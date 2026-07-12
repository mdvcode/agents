#!/usr/bin/env python3
"""Deterministic workflow routing for the agent control plane."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
ROUTING_CONFIG = ROOT / ".agent-routing.yaml"
WORKFLOWS_CONFIG = ROOT / ".agent-workflows.yaml"
POLICY_CONFIG = ROOT / ".agent-policy.yaml"
PROJECT_PROFILES_CONFIG = ROOT / ".agent-project-profiles.yaml"
ROLE_CONTRACTS_CONFIG = ROOT / ".agent-role-contracts.yaml"
REPOSITORIES_CONFIG = ROOT / ".agent-repositories.yaml"
DEFAULT_BUDGETS = {
    "max_roles": 40,
    "max_repair_iterations": 3,
    "max_duration_seconds": 7200,
    "max_tokens": 300000,
}
LOOP_DEFAULTS = {
    "quality_repair": {"from": "quality-runner", "to": "implementation-agent", "max_iterations": 3},
    "review_repair": {"from": "reviewer", "to": "implementation-agent", "max_iterations": 3},
    "ci_repair": {"from": "ci-repair-agent", "to": "quality-runner", "max_iterations": 3},
}
UI_AREAS = {"ui", "routing", "public_rendering", "dashboard_ui", "user_visible_behavior"}
CODE_EXTENSIONS = {
    ".c",
    ".cpp",
    ".go",
    ".java",
    ".js",
    ".jsx",
    ".php",
    ".py",
    ".rb",
    ".rs",
    ".sql",
    ".ts",
    ".tsx",
    ".vue",
}


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        value = yaml.safe_load(handle)
    return value if isinstance(value, dict) else {}


def load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None


def load_workflow_state(run_dir: Path, workflow_state: dict[str, Any] | None) -> dict[str, Any]:
    if workflow_state is not None:
        return workflow_state
    data = load_json(run_dir / "agent_workflow.json")
    return data if isinstance(data, dict) else {}


def _artifact(artifacts_dir: Path, name: str) -> Any:
    return load_json(artifacts_dir / name)


def _role_entries(state: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    roles = state.get("roles", [])
    if not isinstance(roles, list):
        return entries
    for entry in roles:
        if isinstance(entry, dict) and isinstance(entry.get("role"), str):
            entries.append(entry)
        elif isinstance(entry, str):
            entries.append({"role": entry, "result": {"status": "completed"}})
    return entries


def completed_roles(state: dict[str, Any]) -> set[str]:
    completed = {role for role in state.get("completed_roles", []) if isinstance(role, str)}
    for entry in _role_entries(state):
        result = entry.get("result", {})
        if isinstance(result, dict) and result.get("status") == "completed":
            completed.add(str(entry["role"]))
    return completed


def _role_result(state: dict[str, Any], role: str) -> dict[str, Any]:
    for entry in reversed(_role_entries(state)):
        if entry.get("role") == role and isinstance(entry.get("result"), dict):
            return entry["result"]
    return {}


def _list_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if isinstance(item, (str, int))]
    if isinstance(value, str) and value:
        return [value]
    return []


def changed_files(state: dict[str, Any], artifacts_dir: Path) -> list[str]:
    values: set[str] = set()
    for source in (state, _artifact(artifacts_dir, "risk.json"), _artifact(artifacts_dir, "implementation.json")):
        if isinstance(source, dict):
            for key in ("changed_files", "changed_paths"):
                values.update(_list_values(source.get(key)))
    repository = state.get("worktree") or state.get("repository")
    if isinstance(repository, str) and Path(repository).is_dir():
        diff = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
        )
        if diff.returncode == 0:
            values.update(line.strip() for line in diff.stdout.splitlines() if line.strip())
        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
        )
        if untracked.returncode == 0:
            values.update(line.strip() for line in untracked.stdout.splitlines() if line.strip())
    return sorted(values)


def changed_areas(state: dict[str, Any], artifacts_dir: Path) -> set[str]:
    areas: set[str] = set()
    risk = _artifact(artifacts_dir, "risk.json")
    if isinstance(risk, dict):
        areas.update(value.lower() for value in _list_values(risk.get("changed_areas")))
    areas.update(value.lower() for value in _list_values(state.get("changed_areas")))
    files = changed_files(state, artifacts_dir)
    for path in files:
        normalized = path.lower().replace("\\", "/")
        if any(token in normalized for token in ("component", "frontend", "dashboard", "studio", "/ui/", "/app/", "/pages/")):
            areas.add("ui")
        if any(token in normalized for token in ("route", "router", "routing", "urls.py", "next.config", "/app/", "/pages/")):
            areas.add("routing")
        if any(token in normalized for token in ("render", "template", "public/", "page")):
            areas.add("public_rendering")
        if "dashboard" in normalized:
            areas.add("dashboard_ui")
        if Path(normalized).suffix in {".css", ".scss", ".html", ".tsx", ".jsx", ".vue"}:
            areas.add("user_visible_behavior")
    return areas


def ui_changed(state: dict[str, Any], artifacts_dir: Path) -> bool:
    return bool(changed_areas(state, artifacts_dir) & UI_AREAS)


def code_changed(state: dict[str, Any], artifacts_dir: Path) -> bool:
    areas = changed_areas(state, artifacts_dir)
    if areas & {"code", "backend", "implementation", "api", "data", "logic"}:
        return True
    files = changed_files(state, artifacts_dir)
    if not files:
        return False
    return any(Path(path).suffix.lower() in CODE_EXTENSIONS for path in files)


def _blocker_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        for key in ("blocker_ids", "security_blocker_ids", "review_blocker_ids", "blockers", "errors"):
            if key in value:
                return _list_values(value[key])
    return _list_values(value)


def security_blockers(state: dict[str, Any], artifacts_dir: Path) -> list[str]:
    if state.get("security_blockers_present") is True:
        return ["security_blockers_present"]
    security_json = _artifact(artifacts_dir, "security.json")
    if isinstance(security_json, dict):
        blockers = _blocker_values(security_json)
        if blockers or security_json.get("status") in {"fail", "blocked"}:
            return blockers or ["security artifact is blocked"]
    for entry in _role_entries(state):
        if entry.get("role") != "security-agent":
            continue
        result = entry.get("result", {})
        if not isinstance(result, dict):
            continue
        blockers = _blocker_values(result)
        if blockers or result.get("status") in {"failed", "blocked"}:
            return blockers or ["security-agent reported a blocker"]
    return []


def workflow_blockers(state: dict[str, Any], current_result: dict[str, Any]) -> list[str]:
    blockers = _list_values(state.get("blockers"))
    blockers.extend(_list_values(current_result.get("blockers")))
    for entry in _role_entries(state):
        result = entry.get("result", {})
        if isinstance(result, dict):
            blockers.extend(f"{entry['role']}: {value}" for value in _list_values(result.get("blockers")))
    return sorted(set(blockers))


def quality_status(state: dict[str, Any], artifacts_dir: Path) -> str:
    quality = _artifact(artifacts_dir, "quality.json")
    if isinstance(quality, dict) and isinstance(quality.get("overall_status"), str):
        return str(quality["overall_status"])
    if isinstance(state.get("quality_status"), str):
        return str(state["quality_status"])
    result = _role_result(state, "quality-runner")
    if result.get("status") in {"failed", "blocked"}:
        return "fail"
    return ""


def review_status(state: dict[str, Any], artifacts_dir: Path) -> str:
    review = _artifact(artifacts_dir, "review.json")
    if isinstance(review, dict):
        value = review.get("status") or review.get("review_status") or review.get("decision")
        if isinstance(value, str):
            return value.lower()
        if _blocker_values(review):
            return "block"
    if isinstance(state.get("review_status"), str):
        return str(state["review_status"]).lower()
    result = _role_result(state, "reviewer")
    if _blocker_values(result) or result.get("status") in {"failed", "blocked"}:
        return "block"
    return "pass" if result.get("status") == "completed" else ""


def ci_status(state: dict[str, Any], artifacts_dir: Path) -> str:
    quality = _artifact(artifacts_dir, "quality.json")
    for source in (state, quality, _role_result(state, "quality-runner")):
        if isinstance(source, dict):
            value = source.get("ci_status")
            if isinstance(value, str):
                return value.lower()
            if source.get("ci_failed") is True:
                return "fail"
    return ""


def diff_hash(state: dict[str, Any], artifacts_dir: Path) -> str:
    explicit = state.get("diff_hash") or state.get("current_diff_hash")
    if isinstance(explicit, str) and explicit:
        return explicit
    repository = state.get("worktree") or state.get("repository")
    digest = hashlib.sha256()
    if isinstance(repository, str) and Path(repository).is_dir():
        result = subprocess.run(
            ["git", "diff", "HEAD", "--binary"],
            cwd=repository,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            digest.update(result.stdout)
        for path in changed_files(state, artifacts_dir):
            candidate = Path(repository) / path
            if candidate.is_file():
                digest.update(path.encode("utf-8"))
                digest.update(candidate.read_bytes())
    else:
        for path in changed_files(state, artifacts_dir):
            digest.update(path.encode("utf-8"))
    return digest.hexdigest()


def failure_fingerprint(
    *,
    role_result: dict[str, Any],
    state: dict[str, Any],
    artifacts_dir: Path,
) -> str:
    quality = _artifact(artifacts_dir, "quality.json")
    review = _artifact(artifacts_dir, "review.json")
    security = _artifact(artifacts_dir, "security.json")
    failed_commands: list[str] = []
    stderr: list[str] = []
    for source in (role_result, quality if isinstance(quality, dict) else {}, review if isinstance(review, dict) else {}, security if isinstance(security, dict) else {}):
        failed_commands.extend(_list_values(source.get("failed_commands")))
        failed_commands.extend(_list_values(source.get("failed_command")))
        stderr.extend(_list_values(source.get("stderr_excerpt")))
        stderr.extend(_list_values(source.get("stderr")))
        command_results = source.get("commands_attempted", [])
        if not isinstance(command_results, list):
            command_results = []
        for command_result in command_results:
            if isinstance(command_result, dict) and (
                command_result.get("status") in {"fail", "failed"}
                or command_result.get("returncode", 0) not in {0, None}
            ):
                failed_commands.extend(_list_values(command_result.get("command")))
                stderr.extend(_list_values(command_result.get("stderr")))
    payload = {
        "failed_commands": sorted(set(failed_commands)),
        "stderr_excerpt": sorted(set(value[-1000:] for value in stderr)),
        "review_blocker_ids": _blocker_values(review) or _blocker_values(_role_result(state, "reviewer")),
        "security_blocker_ids": _blocker_values(security) or security_blockers(state, artifacts_dir),
        "changed_files": changed_files(state, artifacts_dir),
        "diff_hash": diff_hash(state, artifacts_dir),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _loop_config(name: str, routing: dict[str, Any]) -> dict[str, Any]:
    routing_key = {
        "quality_repair": "quality_failed",
        "review_repair": "review_blocked",
        "ci_repair": "ci_failed",
    }[name]
    configured = routing.get("routing", {}).get(routing_key, {}) if isinstance(routing.get("routing"), dict) else {}
    loop = configured.get("loop", {}) if isinstance(configured, dict) else {}
    default = LOOP_DEFAULTS[name]
    return {
        "name": str(loop.get("name", name)),
        "from": str(loop.get("from", default["from"])),
        "to": str(loop.get("to", default["to"])),
        "max_iterations": int(loop.get("max_iterations", default["max_iterations"])),
    }


def _budgets(state: dict[str, Any], workflows: dict[str, Any]) -> dict[str, int]:
    values = dict(DEFAULT_BUDGETS)
    state_budgets = state.get("budgets")
    workflow_budgets = workflows.get("workflows", {}).get(state.get("workflow", "full_agent_workflow"), {}).get("budgets", {})
    for source in (workflow_budgets, state_budgets):
        if isinstance(source, dict):
            for key in values:
                if isinstance(source.get(key), (int, float)):
                    values[key] = int(source[key])
    return values


def _elapsed_seconds(state: dict[str, Any]) -> float:
    value = state.get("elapsed_seconds")
    if isinstance(value, (int, float)):
        return float(value)
    started_at = state.get("started_at")
    if isinstance(started_at, str):
        try:
            started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            return max(0.0, (datetime.now(timezone.utc) - started).total_seconds())
        except ValueError:
            return 0.0
    return 0.0


def _budget_blockers(state: dict[str, Any], workflows: dict[str, Any]) -> list[str]:
    budgets = _budgets(state, workflows)
    role_count = state.get("role_count", len(_role_entries(state)))
    token_count = state.get("tokens_used")
    if not isinstance(token_count, (int, float)):
        token_count = sum(
            int(entry.get("result", {}).get("tokens_used", 0))
            for entry in _role_entries(state)
            if isinstance(entry.get("result"), dict) and isinstance(entry.get("result", {}).get("tokens_used", 0), (int, float))
        )
    loop_values = state.get("loops", {})
    if not isinstance(loop_values, dict):
        loop_values = {}
    repair_iterations = sum(
        int(value.get("iterations", 0))
        for value in loop_values.values()
        if isinstance(value, dict) and isinstance(value.get("iterations", 0), (int, float))
    )
    blockers: list[str] = []
    if isinstance(role_count, (int, float)) and role_count >= budgets["max_roles"]:
        blockers.append(f"max_roles reached: {int(role_count)} >= {budgets['max_roles']}")
    if _elapsed_seconds(state) >= budgets["max_duration_seconds"]:
        blockers.append(f"max_duration_seconds reached: {int(_elapsed_seconds(state))} >= {budgets['max_duration_seconds']}")
    if token_count >= budgets["max_tokens"]:
        blockers.append(f"max_tokens reached: {int(token_count)} >= {budgets['max_tokens']}")
    if repair_iterations >= budgets["max_repair_iterations"]:
        blockers.append(f"max_repair_iterations reached: {repair_iterations} >= {budgets['max_repair_iterations']}")
    return blockers


def _route(
    next_role: str,
    reason: str,
    *,
    stop: bool = False,
    publication_allowed: bool = False,
    loop: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "next_role": next_role,
        "reason": reason,
        "stop": stop,
        "publication_allowed": publication_allowed,
        "loop": loop,
        "warnings": warnings or [],
    }


def _approval(reason: str, warnings: list[str] | None = None) -> dict[str, Any]:
    return _route("approval-gate", reason, stop=True, warnings=warnings)


def _missing_required_gates(state: dict[str, Any], artifacts_dir: Path, current_role: str) -> list[str]:
    routing = load_yaml(ROUTING_CONFIG)
    required = routing.get("required_before_publication", [])
    if not isinstance(required, list):
        required = []
    completed = completed_roles(state)
    missing: list[str] = []
    artifact_map = {
        "issue-intake": ("issue.json",),
        "context-compiler": (),
        "planner": ("plan.md", "project_profile.json"),
        "risk-classifier": ("risk.json",),
        "implementation-agent": ("implementation.json",),
        "quality-runner": ("quality.json",),
        "security-agent": ("security.md", "security.json"),
        "reviewer": ("review.md",),
        "orchestrator": ("verdict.json",),
        "publication-prepare": ("change_set.json", "publication_payload.json"),
    }
    for role in required:
        if role == "publication-prepare" and current_role != "publication-prepare":
            continue
        if role == current_role:
            continue
        if str(role) not in completed:
            missing.append(str(role))
    return missing


def _invalid_required_gates(state: dict[str, Any], artifacts_dir: Path, current_role: str) -> list[str]:
    completed = completed_roles(state)
    schema_by_role = {
        "planner": ("project_profile.json", ROOT / "schemas" / "project_profile.schema.json"),
        "risk-classifier": ("risk.json", ROOT / "schemas" / "risk.schema.json"),
        "quality-runner": ("quality.json", ROOT / "schemas" / "quality.schema.json"),
        "orchestrator": ("verdict.json", ROOT / "schemas" / "verdict.schema.json"),
        "publication-prepare": ("change_set.json", ROOT / "schemas" / "change_set.schema.json"),
    }
    plain_artifacts = {
        "planner": ("plan.md",),
        "security-agent": ("security.md",),
        "reviewer": ("review.md",),
        "publication-prepare": ("publication_payload.json",),
    }
    invalid: list[str] = []
    routing = load_yaml(ROUTING_CONFIG)
    required = routing.get("required_before_publication", [])
    if not isinstance(required, list):
        return invalid
    from adapters.codex_adapter import contract_section
    from validate_artifacts import validate_required

    for role in required:
        role_name = str(role)
        if role_name == "publication-prepare" and current_role != "publication-prepare":
            continue
        if role_name == current_role:
            continue
        if role_name not in completed:
            continue
        schema_entry = schema_by_role.get(role_name)
        if schema_entry is not None:
            artifact_name, schema_path = schema_entry
            value = _artifact(artifacts_dir, artifact_name)
            schema = load_json(schema_path)
            if not isinstance(value, dict) or not isinstance(schema, dict):
                invalid.append(role_name)
            else:
                errors = validate_required(value, contract_section(schema, "artifact"), artifact_name)
                if errors:
                    invalid.append(role_name)
        for artifact_name in plain_artifacts.get(role_name, ()):
            path = artifacts_dir / artifact_name
            if not path.exists() or path.stat().st_size == 0:
                invalid.append(role_name)
    return sorted(set(invalid), key=lambda role: required.index(role) if role in required else 0)


def _repair_route(
    name: str,
    *,
    state: dict[str, Any],
    role_result: dict[str, Any],
    artifacts_dir: Path,
    routing: dict[str, Any],
) -> dict[str, Any]:
    config = _loop_config(name, routing)
    loops = state.get("loops")
    if not isinstance(loops, dict):
        loops = {}
        state["loops"] = loops
    previous = loops.get(name, {}) if isinstance(loops.get(name), dict) else {}
    iteration = int(previous.get("iterations", 0)) + 1
    fingerprint = failure_fingerprint(role_result=role_result, state=state, artifacts_dir=artifacts_dir)
    current_diff = diff_hash(state, artifacts_dir)
    progress = not (
        previous.get("last_failure_fingerprint") == fingerprint
        and previous.get("last_diff_fingerprint") == current_diff
    )
    loop = {
        "name": config["name"],
        "iteration": iteration,
        "max_iterations": config["max_iterations"],
        "failure_fingerprint": fingerprint,
        "progress_detected": progress,
    }
    loops[name] = {
        "iterations": iteration,
        "max_iterations": config["max_iterations"],
        "last_failure_fingerprint": fingerprint,
        "last_diff_fingerprint": current_diff,
        "progress_detected": progress,
    }
    if iteration >= config["max_iterations"] or not progress:
        return _approval(
            f"{config['name']} stopped after a repeated failure without sufficient progress.",
            [f"{config['name']} iteration {iteration} of {config['max_iterations']}"],
        ) | {"loop": loop}
    return _route(
        config["to"],
        f"{config['name']} repair iteration {iteration} started.",
        loop=loop,
    )


def decide_next_role(
    *,
    current_role: str,
    role_result: dict[str, Any] | None,
    run_dir: Path,
    artifacts_dir: Path,
    workflow_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the authoritative next route using only deterministic state and artifacts."""
    state = load_workflow_state(run_dir, workflow_state)
    result = role_result if isinstance(role_result, dict) else {}
    routing = load_yaml(ROUTING_CONFIG)
    workflows = load_yaml(WORKFLOWS_CONFIG)
    # Load all control-plane sources explicitly so routing never depends on role text alone.
    load_yaml(POLICY_CONFIG)
    load_yaml(PROJECT_PROFILES_CONFIG)
    load_yaml(ROLE_CONTRACTS_CONFIG)
    load_yaml(REPOSITORIES_CONFIG)
    warnings: list[str] = []
    advisory = result.get("next_action")

    if advisory and advisory not in {"continue", current_role}:
        warnings.append(f"Ignored advisory next_action={advisory!r}; deterministic routing is authoritative.")

    risk = _artifact(artifacts_dir, "risk.json")
    risk_class = risk.get("risk_class") if isinstance(risk, dict) else state.get("risk_class")
    if risk_class == "high":
        return _approval("Risk class is HIGH. Publication is not allowed without human approval.", warnings)

    security = security_blockers(state, artifacts_dir)
    if security:
        return _approval("Security blockers are present. Publication is stopped pending human approval.", warnings + security)

    budget_blockers = _budget_blockers(state, workflows)
    if budget_blockers:
        return _approval("Workflow budget exceeded; execution is awaiting approval.", warnings + budget_blockers)

    quality = quality_status(state, artifacts_dir)
    if current_role == "quality-runner" and quality == "fail":
        if ci_status(state, artifacts_dir) == "fail":
            route = _repair_route("ci_repair", state=state, role_result=result, artifacts_dir=artifacts_dir, routing=routing)
        else:
            route = _repair_route("quality_repair", state=state, role_result=result, artifacts_dir=artifacts_dir, routing=routing)
        if not route["stop"]:
            after_loop_budget = _budget_blockers(state, workflows)
            if after_loop_budget:
                return _approval("Workflow repair budget exceeded; execution is awaiting approval.", after_loop_budget) | {"loop": route.get("loop")}
            if ci_status(state, artifacts_dir) == "fail":
                route["next_role"] = "ci-repair-agent"
                route["reason"] = "CI checks failed; starting bounded CI repair loop."
        return route
    if current_role == "reviewer" and review_status(state, artifacts_dir) == "block":
        route = _repair_route("review_repair", state=state, role_result=result, artifacts_dir=artifacts_dir, routing=routing)
        if not route["stop"]:
            after_loop_budget = _budget_blockers(state, workflows)
            if after_loop_budget:
                return _approval("Workflow repair budget exceeded; execution is awaiting approval.", after_loop_budget) | {"loop": route.get("loop")}
        return route
    if current_role == "ci-repair-agent":
        return _route("quality-runner", "CI repair completed; quality must be re-run.", warnings=warnings)

    blockers = workflow_blockers(state, result)
    if blockers:
        return _approval("Workflow blockers are present; execution is awaiting approval.", warnings + blockers)

    if result.get("status") in {"blocked", "failed", "awaiting_approval"}:
        return _approval(f"Role {current_role} did not complete successfully.", warnings + _list_values(result.get("blockers")))

    code = code_changed(state, artifacts_dir)
    ui = ui_changed(state, artifacts_dir)
    next_role = ""
    reason = ""
    if current_role == "issue-intake":
        next_role, reason = "context-compiler", "Issue intake is recorded; compile scoped context."
    elif current_role == "context-compiler":
        next_role, reason = "planner", "Context is available; planning is the next required gate."
    elif current_role == "planner":
        next_role, reason = "risk-classifier", "Planner output is advisory; risk classification is the next required gate."
    elif current_role == "risk-classifier":
        next_role, reason = "implementation-agent", "Risk is below HIGH; implementation is the next required gate."
    elif current_role == "implementation-agent":
        next_role, reason = "quality-runner", "Implementation completed; quality checks are the next required gate."
    elif current_role == "test-generator":
        next_role, reason = "quality-runner", "Tests are recorded; quality checks are required."
    elif current_role == "quality-runner":
        next_role, reason = "security-agent", "Quality passed; security is the next required gate."
    elif current_role == "security-agent":
        if ui:
            next_role, reason = "frontend-qa-agent", "User-visible or UI files changed; visual evidence gate is required."
        elif code:
            next_role, reason = "architecture-consistency-agent", "Code changed; architecture consistency gate is required."
        else:
            next_role, reason = "reviewer", "No optional UI or code-impacting gate is required; review is next."
    elif current_role == "frontend-qa-agent":
        if code:
            next_role, reason = "architecture-consistency-agent", "Frontend evidence is recorded; code-impacting architecture gate is required."
        else:
            next_role, reason = "reviewer", "Frontend evidence is recorded; review is next."
    elif current_role == "architecture-consistency-agent":
        if code:
            next_role, reason = "semantic-conflict-agent", "Architecture check is recorded; semantic conflict gate is required."
        else:
            next_role, reason = "reviewer", "Architecture check is recorded; review is next."
    elif current_role == "semantic-conflict-agent":
        next_role, reason = "reviewer", "Semantic conflict check is recorded; review is next."
    elif current_role == "reviewer":
        next_role, reason = "orchestrator", "Review passed; orchestrator must make the final workflow verdict."
    elif current_role == "orchestrator":
        missing = _missing_required_gates(state, artifacts_dir, current_role)
        if missing:
            return _route(missing[0], f"Required gate {missing[0]} is missing before publication preparation.", warnings=warnings)
        invalid = _invalid_required_gates(state, artifacts_dir, current_role)
        if invalid:
            return _route(invalid[0], f"Required gate {invalid[0]} has invalid artifacts before publication preparation.", warnings=warnings)
        next_role, reason = "publication-prepare", "Required gates passed; prepare the publication inputs."
        return _route(next_role, reason, publication_allowed=risk_class in {"low", "medium"}, warnings=warnings)
    elif current_role == "publication-prepare":
        missing = _missing_required_gates(state, artifacts_dir, current_role)
        if missing:
            return _route(missing[0], f"Required gate {missing[0]} is missing; publication remains unreachable.", warnings=warnings)
        invalid = _invalid_required_gates(state, artifacts_dir, current_role)
        if invalid:
            return _route(invalid[0], f"Required gate {invalid[0]} has invalid artifacts; publication remains unreachable.", warnings=warnings)
        if risk_class not in {"low", "medium"}:
            return _approval("Publication requires LOW or MEDIUM risk.", warnings)
        visual_required = ui
        visual = _artifact(artifacts_dir, "frontend_qa.json")
        visual_present = isinstance(visual, dict) and visual.get("evidence_collected") is True
        if visual_required and not visual_present:
            warnings.append("Frontend evidence is missing; publication may proceed only as a draft.")
        next_role, reason = "publication", "LOW/MEDIUM risk and all required gates passed."
        return _route(next_role, reason, publication_allowed=True, warnings=warnings)
    elif current_role == "publication":
        return _route("", "Publication executor completed.", stop=True, publication_allowed=True, warnings=warnings)
    else:
        return _approval(f"Unknown workflow role {current_role!r}; routing cannot continue safely.", warnings)

    return _route(next_role, reason, warnings=warnings)


__all__ = [
    "changed_areas",
    "changed_files",
    "code_changed",
    "decide_next_role",
    "diff_hash",
    "failure_fingerprint",
    "load_yaml",
    "security_blockers",
    "ui_changed",
    "workflow_blockers",
]

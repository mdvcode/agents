#!/usr/bin/env python3
"""Deterministic workflow routing for the agent control plane."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ai_harness.economics import BudgetAction, BudgetController, BudgetUsage
from security_approval import scope_accepts_security, security_finding_ids


ROOT = Path(__file__).resolve().parents[1]
ROUTING_CONFIG = ROOT / ".agent-routing.yaml"
WORKFLOWS_CONFIG = ROOT / ".agent-workflows.yaml"
POLICY_CONFIG = ROOT / ".agent-policy.yaml"
PROJECT_PROFILES_CONFIG = ROOT / ".agent-project-profiles.yaml"
ROLE_CONTRACTS_CONFIG = ROOT / ".agent-role-contracts.yaml"
REPOSITORIES_CONFIG = ROOT / ".agent-repositories.yaml"
DEFAULT_BUDGETS = {
    "max_roles": 40,
    "max_repair_iterations": 12,
    "max_duration_seconds": 3600,
    "max_tokens": 300000,
}
LOOP_DEFAULTS = {
    "security_repair": {"from": "security-agent", "to": "implementation-agent", "max_iterations": 3, "max_tokens": 60000, "max_duration_seconds": 1800},
    "quality_repair": {"from": "quality-runner", "to": "implementation-agent", "max_iterations": 3, "max_tokens": 60000, "max_duration_seconds": 1800},
    "review_repair": {"from": "reviewer", "to": "implementation-agent", "max_iterations": 3, "max_tokens": 60000, "max_duration_seconds": 1800},
    "ci_repair": {"from": "ci-repair-agent", "to": "quality-runner", "max_iterations": 3, "max_tokens": 60000, "max_duration_seconds": 1800},
    "frontend_verification_repair": {"from": "frontend-qa-agent", "to": "implementation-agent", "max_iterations": 3, "max_tokens": 60000, "max_duration_seconds": 1800},
}
UI_AREAS = {"ui", "routing", "public_rendering", "dashboard_ui", "user_visible_behavior"}
SECURITY_SEVERITY_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
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
FAST_REQUIRED_ROLES = [
    "issue-intake",
    "context-compiler",
    "implementation-agent",
    "quality-runner",
    "security-agent",
    "reviewer",
    "orchestrator",
    "publication-prepare",
]
FAST_SENSITIVE_PARTS = {
    "auth",
    "billing",
    "payments",
    "credentials",
    "secrets",
    "migrations",
    "terraform",
    "k8s",
    "infra/prod",
}


def execution_mode(state: dict[str, Any]) -> str:
    value = str(state.get("effective_mode", "full"))
    return value if value in {"adaptive", "fast", "full", "goal"} else "full"


def execution_plan(state: dict[str, Any]) -> dict[str, Any]:
    path = state.get("execution_plan_path")
    if not isinstance(path, str) or not path:
        return {}
    value = load_json(Path(path))
    return value if isinstance(value, dict) else {}


def adaptive_next_role(
    state: dict[str, Any],
    *,
    current_role: str,
) -> tuple[str, str] | None:
    """Return the next ready DAG node while repair and hard gates stay authoritative."""

    plan = execution_plan(state)
    nodes = plan.get("nodes", [])
    if not isinstance(nodes, list) or not nodes:
        return None
    if current_role == "implementation-agent":
        # Only the route that immediately dispatched this implementation run may
        # request a verifier re-run. Historical loop counters remain non-zero for
        # audit purposes and must not keep pulling the DAG back into an old loop.
        last_route = state.get("last_route", {})
        active_loop = last_route.get("loop", {}) if isinstance(last_route, dict) else {}
        loop_name = str(active_loop.get("name", "")) if isinstance(active_loop, dict) else ""
        repair_targets = {
            "security_repair": "security-agent",
            "quality_repair": "quality-runner",
            "review_repair": "reviewer",
            "frontend_verification_repair": "frontend-qa-agent",
        }
        target = repair_targets.get(loop_name)
        if target:
            return target, f"Adaptive {loop_name} requires re-running {target}."
    completed = completed_roles(state)
    skipped = state.get("budget_skipped_roles", [])
    if isinstance(skipped, list):
        completed.update(str(role) for role in skipped if isinstance(role, str))
    controller = BudgetController.from_plan(plan)
    usage = BudgetUsage.from_state(state)
    for raw in nodes:
        if not isinstance(raw, dict):
            continue
        node_id = str(raw.get("id", ""))
        role = str(raw.get("role", node_id))
        dependencies = {
            str(value)
            for value in raw.get("dependencies", [])
            if isinstance(value, str)
        }
        if not node_id or role in completed or role == current_role:
            continue
        if dependencies <= completed:
            mandatory = bool(raw.get("mandatory", False))
            decision = controller.assess(usage, mandatory_role=mandatory)
            state["budget_action"] = decision.as_dict()
            state["budget_usage"] = usage.as_dict()
            if decision.action == BudgetAction.SKIP_OPTIONAL and not mandatory:
                skipped_roles = state.setdefault("budget_skipped_roles", [])
                if isinstance(skipped_roles, list) and role not in skipped_roles:
                    skipped_roles.append(role)
                completed.add(role)
                continue
            return role, f"Adaptive execution plan selected ready node {node_id}."
    return None


def fast_path_blockers(state: dict[str, Any], artifacts_dir: Path) -> list[str]:
    files = changed_files(state, artifacts_dir)
    blockers: list[str] = []
    if len(files) > 5:
        blockers.append(f"fast path allows at most 5 changed files; observed {len(files)}")
    for value in files:
        normalized = value.replace("\\", "/").lower()
        parts = set(Path(normalized).parts)
        if (
            normalized.startswith(".env")
            or normalized.endswith((".pem", ".key"))
            or any(token in normalized for token in FAST_SENSITIVE_PARTS)
            or {"settings_prod.py", "production.py"} & parts
        ):
            blockers.append(f"fast path cannot verify sensitive path: {value}")
    implementation = _artifact(artifacts_dir, "implementation.json")
    if isinstance(implementation, dict) and implementation.get("risk_changed") is True:
        blockers.append("implementation reported that the planned risk changed")
    risk = _artifact(artifacts_dir, "risk.json")
    if isinstance(risk, dict) and risk.get("risk_class") not in {None, "low"}:
        blockers.append(f"fast path requires LOW risk; observed {risk.get('risk_class')}")
    repository = state.get("worktree") or state.get("repository")
    if isinstance(repository, str) and Path(repository).is_dir():
        diff = subprocess.run(
            ["git", "diff", "--numstat", "HEAD"],
            cwd=repository,
            text=True,
            capture_output=True,
            check=False,
        )
        changed_lines = 0
        if diff.returncode == 0:
            for line in diff.stdout.splitlines():
                columns = line.split("\t", 2)
                if len(columns) >= 2 and columns[0].isdigit() and columns[1].isdigit():
                    changed_lines += int(columns[0]) + int(columns[1])
        if changed_lines > 200:
            blockers.append(f"fast path allows at most 200 changed lines; observed {changed_lines}")
    return blockers


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
    data = load_json(run_dir / "workflow.json")
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


def changed_line_count(state: dict[str, Any]) -> int:
    repository = state.get("checkout_path") or state.get("worktree") or state.get("repository")
    if not isinstance(repository, str) or not Path(repository).is_dir():
        return 0
    result = subprocess.run(
        ["git", "diff", "--numstat", "HEAD"],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    total = 0
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            columns = line.split("\t", 2)
            if len(columns) >= 2 and columns[0].isdigit() and columns[1].isdigit():
                total += int(columns[0]) + int(columns[1])
    return total


def risk_class_value(state: dict[str, Any], artifacts_dir: Path) -> str:
    risk = _artifact(artifacts_dir, "risk.json")
    if isinstance(risk, dict) and isinstance(risk.get("risk_class"), str):
        return str(risk["risk_class"])
    return str(state.get("risk_class", "low"))


def architecture_review_required(state: dict[str, Any], artifacts_dir: Path) -> bool:
    files = changed_files(state, artifacts_dir)
    if len(files) > 5 or changed_line_count(state) > 200:
        return True
    markers = (
        "architecture",
        "contract",
        "migration",
        "model",
        "pyproject",
        "routing",
        "schema",
        "settings",
        "workflow",
    )
    return any(any(marker in path.casefold() for marker in markers) for path in files)


def semantic_review_required(state: dict[str, Any], artifacts_dir: Path) -> bool:
    if risk_class_value(state, artifacts_dir) not in {"medium", "high"}:
        return False
    if not code_changed(state, artifacts_dir):
        return False
    markers = (
        "api",
        "contract",
        "domain",
        "model",
        "permission",
        "serializer",
        "service",
        "schema",
    )
    files = changed_files(state, artifacts_dir)
    return len(files) > 3 or any(
        any(marker in path.casefold() for marker in markers) for path in files
    )


def reviewer_requires_llm(state: dict[str, Any], artifacts_dir: Path) -> bool:
    files = changed_files(state, artifacts_dir)
    risk_class = risk_class_value(state, artifacts_dir)
    return (
        risk_class in {"medium", "high"}
        or code_changed(state, artifacts_dir)
        or ui_changed(state, artifacts_dir)
        or len(files) > 5
        or changed_line_count(state) > 200
    )


def _blocker_values(value: Any) -> list[str]:
    if isinstance(value, dict):
        for key in ("blocker_ids", "security_blocker_ids", "review_blocker_ids", "blockers", "errors"):
            if key in value:
                return _list_values(value[key])
    return _list_values(value)


def security_severity(state: dict[str, Any], artifacts_dir: Path) -> str:
    values: list[str] = []
    if state.get("security_blockers_present") is True:
        values.append("critical")
    explicit = state.get("security_highest_severity")
    if isinstance(explicit, str):
        values.append(explicit.lower())
    security_json = _artifact(artifacts_dir, "security.json")
    if isinstance(security_json, dict):
        highest = security_json.get("highest_severity")
        if isinstance(highest, str):
            values.append(highest.lower())
        findings = security_json.get("findings", [])
        if isinstance(findings, list):
            values.extend(
                str(finding.get("severity", "")).lower()
                for finding in findings
                if isinstance(finding, dict)
            )
    recognized = [value for value in values if value in SECURITY_SEVERITY_RANK]
    if recognized:
        return max(recognized, key=SECURITY_SEVERITY_RANK.__getitem__)
    if isinstance(security_json, dict) and (
        security_json.get("status") in {"fail", "blocked"}
        or security_json.get("verdict") == "broken"
    ):
        return "critical"
    return "none"


def security_blockers(state: dict[str, Any], artifacts_dir: Path) -> list[str]:
    severity = security_severity(state, artifacts_dir)
    security_json = _artifact(artifacts_dir, "security.json")
    if isinstance(security_json, dict):
        blockers = _blocker_values(security_json)
        severity_requires_stop = (
            SECURITY_SEVERITY_RANK[severity] >= SECURITY_SEVERITY_RANK["medium"]
        )
        artifact_failed = (
            security_json.get("status") in {"fail", "blocked"}
            or security_json.get("verdict") == "broken"
        )
        if blockers or severity_requires_stop or artifact_failed:
            return blockers or ["security artifact is blocked"]
    if state.get("security_blockers_present") is True:
        return ["security_blockers_present"]
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


def accepted_security_finding_ids(state: dict[str, Any], artifacts_dir: Path) -> set[str]:
    security = _artifact(artifacts_dir, "security.json")
    if not isinstance(security, dict):
        return set()
    grants = state.get("approval_grants", [])
    if not isinstance(grants, list):
        return set()
    for grant in grants:
        if not isinstance(grant, dict) or grant.get("gate") != "security-agent":
            continue
        scope = grant.get("scope", {})
        if not isinstance(scope, dict):
            continue
        if (
            "accept_security_finding" in scope.get("actions", [])
            and scope_accepts_security(scope, security)
        ):
            return set(security_finding_ids(security))
    return set()


def security_acceptance_granted(state: dict[str, Any], artifacts_dir: Path) -> bool:
    return bool(accepted_security_finding_ids(state, artifacts_dir))


def _artifact_resolves_role_blockers(role: str, artifacts_dir: Path) -> bool:
    artifact_names = {
        "quality-runner": "quality.json",
        "security-agent": "security.json",
        "frontend-qa-agent": "frontend_qa.json",
        "architecture-consistency-agent": "architecture_consistency.json",
        "semantic-conflict-agent": "semantic_conflict.json",
        "reviewer": "review.json",
    }
    artifact_name = artifact_names.get(role)
    if not artifact_name:
        return False
    value = _artifact(artifacts_dir, artifact_name)
    if not isinstance(value, dict) or _blocker_values(value):
        return False
    if role == "quality-runner":
        return value.get("overall_status") in {"pass", "warn"}
    return value.get("verdict") == "works" or value.get("status") in {"pass", "warn"}


def workflow_blockers(
    state: dict[str, Any],
    current_result: dict[str, Any],
    artifacts_dir: Path,
    current_role: str,
) -> list[str]:
    accepted_ids = accepted_security_finding_ids(state, artifacts_dir)
    active_repair_sources: set[str] = set()
    entries = _role_entries(state)
    last_positions = {
        role: max(index for index, entry in enumerate(entries) if entry.get("role") == role)
        for role in {str(entry.get("role", "")) for entry in entries}
    }
    implementation_position = last_positions.get("implementation-agent", -1)
    loop_sources = {
        "quality_repair": {"quality-runner"},
        "security_repair": {"security-agent"},
        "review_repair": {
            "architecture-consistency-agent",
            "semantic-conflict-agent",
            "reviewer",
        },
        "frontend_verification_repair": {"frontend-qa-agent"},
    }
    loops = state.get("loops", {})
    if isinstance(loops, dict):
        for loop_name, sources in loop_sources.items():
            loop = loops.get(loop_name, {})
            if not isinstance(loop, dict) or int(loop.get("iterations", 0) or 0) <= 0:
                continue
            if any(last_positions.get(source, -1) < implementation_position for source in sources):
                active_repair_sources.update(sources)

    def unresolved(values: Any) -> list[str]:
        return [
            value
            for value in _list_values(values)
            if not any(
                re.search(
                    rf"(?<![A-Za-z0-9_-]){re.escape(finding_id)}(?![A-Za-z0-9_-])",
                    value,
                )
                for finding_id in accepted_ids
            )
        ]

    blockers = unresolved(state.get("blockers"))
    if not verifier_unavailability_accepted(state, artifacts_dir, current_role):
        blockers.extend(unresolved(current_result.get("blockers")))
    latest_results: dict[str, dict[str, Any]] = {}
    for entry in _role_entries(state):
        role = str(entry["role"])
        result = entry.get("result", {})
        if isinstance(result, dict):
            latest_results[role] = result
    for role, result in latest_results.items():
        # Approval-gate blockers explain why execution paused. Once a scoped
        # approval is consumed they are historical control-plane evidence, not
        # unresolved work. Likewise, a successful rerun supersedes an older
        # failed result for the same role.
        if role == "approval-gate":
            continue
        if role in active_repair_sources:
            continue
        if verifier_unavailability_accepted(state, artifacts_dir, role):
            continue
        if _artifact_resolves_role_blockers(role, artifacts_dir):
            continue
        blockers.extend(f"{role}: {value}" for value in unresolved(result.get("blockers")))
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
        value = review.get("verdict") or review.get("status") or review.get("review_status") or review.get("decision")
        if isinstance(value, str):
            normalized = value.lower()
            return "block" if normalized == "broken" else normalized
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


def verifier_verdict(artifacts_dir: Path, artifact_name: str) -> str:
    artifact = _artifact(artifacts_dir, artifact_name)
    if not isinstance(artifact, dict):
        return ""
    verdict = artifact.get("verdict")
    if isinstance(verdict, str):
        return verdict.lower()
    if artifact_name == "frontend_qa.json":
        if artifact.get("evidence_collected") is True:
            return "works"
        if artifact.get("evidence_required") is True:
            return "unavailable"
    return ""


def verifier_environment_unavailable(artifacts_dir: Path, artifact_name: str) -> bool:
    artifact = _artifact(artifacts_dir, artifact_name)
    if not isinstance(artifact, dict):
        return False
    markers = (
        "unavailable",
        "missing dependenc",
        "command not found",
        "browser",
        "playwright",
        "read-only",
        "runtime capability",
        "did not complete",
    )
    if str(artifact.get("verdict", "")).lower() == "unavailable":
        return True
    blockers = [
        str(item).lower()
        for item in artifact.get("blockers", [])
        if isinstance(item, (str, int)) and str(item).strip()
    ]
    if blockers:
        return all(any(marker in blocker for marker in markers) for blocker in blockers)
    fallback = " ".join(
        [
            *[str(item) for item in artifact.get("warnings", []) if isinstance(item, (str, int))],
            *[str(item) for item in artifact.get("observed", []) if isinstance(item, (str, int))],
        ]
    ).lower()
    return any(marker in fallback for marker in markers)


def verifier_artifact_fingerprint(artifacts_dir: Path, artifact_name: str) -> str:
    artifact = _artifact(artifacts_dir, artifact_name)
    if not isinstance(artifact, dict):
        return ""
    return hashlib.sha256(
        json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def verifier_unavailability_accepted(
    state: dict[str, Any],
    artifacts_dir: Path,
    role: str,
) -> bool:
    artifact_names = {
        "architecture-consistency-agent": "architecture_consistency.json",
        "semantic-conflict-agent": "semantic_conflict.json",
        "reviewer": "review.json",
    }
    artifact_name = artifact_names.get(role)
    if not artifact_name or not verifier_environment_unavailable(artifacts_dir, artifact_name):
        return False
    fingerprint = verifier_artifact_fingerprint(artifacts_dir, artifact_name)
    grants = state.get("approval_grants", [])
    if not isinstance(grants, list):
        return False
    for grant in grants:
        if not isinstance(grant, dict) or grant.get("gate") != role:
            continue
        scope = grant.get("scope", {})
        if not isinstance(scope, dict):
            continue
        actions = scope.get("actions", [])
        if (
            "accept_unavailable_verification" in actions
            and scope.get("verifier_fingerprint") == fingerprint
        ):
            return True
        # Backward compatibility for explicit grants created before verifier
        # artifact fingerprints were added.
        reason = str(grant.get("reason", "")).lower()
        if (
            "resume_workflow" in actions
            and not scope.get("verifier_fingerprint")
            and (
                "could not verify" in reason
                or "unavailable" in reason
                or (role == "reviewer" and "workflow blockers" in reason)
            )
        ):
            return True
    return False


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
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _loop_config(name: str, routing: dict[str, Any]) -> dict[str, Any]:
    routing_key = {
        "security_repair": "security_failed",
        "quality_repair": "quality_failed",
        "review_repair": "review_blocked",
        "ci_repair": "ci_failed",
        "frontend_verification_repair": "frontend_verification_failed",
    }[name]
    configured = routing.get("routing", {}).get(routing_key, {}) if isinstance(routing.get("routing"), dict) else {}
    loop = configured.get("loop", {}) if isinstance(configured, dict) else {}
    default = LOOP_DEFAULTS[name]
    return {
        "name": str(loop.get("name", name)),
        "from": str(loop.get("from", default["from"])),
        "to": str(loop.get("to", default["to"])),
        "max_iterations": int(loop.get("max_iterations", default["max_iterations"])),
        "max_tokens": int(loop.get("max_tokens", default["max_tokens"])),
        "max_duration_seconds": int(loop.get("max_duration_seconds", default["max_duration_seconds"])),
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


def _blocked(reason: str, warnings: list[str] | None = None) -> dict[str, Any]:
    return _route("blocked", reason, stop=True, warnings=warnings)


def required_gate_roles(state: dict[str, Any], artifacts_dir: Path) -> list[str]:
    if execution_mode(state) == "adaptive":
        plan = execution_plan(state)
        configured = plan.get("required_roles", [])
        if isinstance(configured, list) and configured:
            skipped = {
                str(role)
                for role in state.get("budget_skipped_roles", [])
                if isinstance(role, str)
            }
            mandatory = {
                str(node.get("role", node.get("id", "")))
                for node in plan.get("nodes", [])
                if isinstance(node, dict) and node.get("mandatory") is True
            }
            return [
                str(role)
                for role in configured
                if isinstance(role, str) and (role not in skipped or role in mandatory)
            ]
    if execution_mode(state) == "fast":
        return list(FAST_REQUIRED_ROLES)
    routing = load_yaml(ROUTING_CONFIG)
    configured = routing.get("required_before_publication", [])
    required = [str(role) for role in configured if isinstance(role, str)] if isinstance(configured, list) else []
    if not code_changed(state, artifacts_dir) and "test-generator" in required:
        required.remove("test-generator")
    optional: list[str] = []
    if ui_changed(state, artifacts_dir):
        optional.append("frontend-qa-agent")
    if architecture_review_required(state, artifacts_dir):
        optional.append("architecture-consistency-agent")
    if semantic_review_required(state, artifacts_dir):
        optional.append("semantic-conflict-agent")
    insert_at = required.index("reviewer") if "reviewer" in required else len(required)
    for role in optional:
        if role not in required:
            required.insert(insert_at, role)
            insert_at += 1
    return required


def _missing_required_gates(state: dict[str, Any], artifacts_dir: Path, current_role: str) -> list[str]:
    required = required_gate_roles(state, artifacts_dir)
    completed = completed_roles(state)
    missing: list[str] = []
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
        "security-agent": ("security.json", ROOT / "schemas" / "security.schema.json"),
        "frontend-qa-agent": ("frontend_qa.json", ROOT / "schemas" / "roles" / "frontend-qa-agent.schema.json"),
        "architecture-consistency-agent": ("architecture_consistency.json", ROOT / "schemas" / "roles" / "architecture-consistency.schema.json"),
        "semantic-conflict-agent": ("semantic_conflict.json", ROOT / "schemas" / "roles" / "semantic-conflict.schema.json"),
        "reviewer": ("review.json", ROOT / "schemas" / "review.schema.json"),
        "orchestrator": ("verdict.json", ROOT / "schemas" / "verdict.schema.json"),
        "publication-prepare": ("change_set.json", ROOT / "schemas" / "change_set.schema.json"),
    }
    plain_artifacts = {
        "issue-intake": ("issue.json",),
        "planner": ("plan.md",),
        "implementation-agent": ("implementation.json",),
        "test-generator": ("test_plan.json", "test_result.json"),
        "publication-prepare": ("publication_payload.json",),
    }
    invalid: list[str] = []
    required = required_gate_roles(state, artifacts_dir)
    from runtime_contracts import contract_section
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
                if role_name in {
                    "security-agent",
                    "architecture-consistency-agent",
                    "semantic-conflict-agent",
                    "reviewer",
                } and value.get("verdict") != "works" and not (
                    role_name == "security-agent"
                    and security_acceptance_granted(state, artifacts_dir)
                ) and not (
                    role_name in {
                        "architecture-consistency-agent",
                        "semantic-conflict-agent",
                        "reviewer",
                    }
                    and verifier_unavailability_accepted(state, artifacts_dir, role_name)
                ):
                    invalid.append(role_name)
                if role_name == "frontend-qa-agent" and value.get("verdict") not in {"works", "unavailable"}:
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
    approval_consumed: bool = False,
) -> dict[str, Any]:
    config = _loop_config(name, routing)
    loops = state.get("loops")
    if not isinstance(loops, dict):
        loops = {}
        state["loops"] = loops
    previous = loops.get(name, {}) if isinstance(loops.get(name), dict) else {}
    iteration = min(
        int(previous.get("iterations", 0)) + 1,
        config["max_iterations"],
    )
    fingerprint = failure_fingerprint(role_result=role_result, state=state, artifacts_dir=artifacts_dir)
    current_diff = diff_hash(state, artifacts_dir)
    total_tokens = int(state.get("tokens_used", 0) or 0)
    elapsed = _elapsed_seconds(state)
    tokens_at_start = int(previous.get("tokens_at_start", total_tokens) or 0)
    elapsed_at_start = float(previous.get("elapsed_at_start", elapsed) or 0)
    loop_tokens = max(0, total_tokens - tokens_at_start)
    loop_elapsed = max(0, int(elapsed - elapsed_at_start))
    progress = not (
        previous.get("last_failure_fingerprint") == fingerprint
        and previous.get("last_diff_fingerprint") == current_diff
    )
    loop = {
        "name": config["name"],
        "iteration": iteration,
        "max_iterations": config["max_iterations"],
        "failure_fingerprint": fingerprint,
        "diff_fingerprint": current_diff,
        "progress_detected": progress,
        "tokens_used": loop_tokens,
        "elapsed_seconds": loop_elapsed,
    }
    loops[name] = {
        "iterations": iteration,
        "max_iterations": config["max_iterations"],
        "last_failure_fingerprint": fingerprint,
        "last_diff_fingerprint": current_diff,
        "progress_detected": progress,
        "tokens_at_start": tokens_at_start,
        "elapsed_at_start": elapsed_at_start,
        "tokens_used": loop_tokens,
        "elapsed_seconds": loop_elapsed,
    }
    exhausted = (
        iteration >= config["max_iterations"]
        or loop_tokens >= config["max_tokens"]
        or loop_elapsed >= config["max_duration_seconds"]
    )
    if exhausted or not progress:
        reason = f"{config['name']} stopped after a repeated failure or loop budget exhaustion."
        loop_warnings = [
            f"{config['name']} iteration {iteration} of {config['max_iterations']}",
            f"tokens {loop_tokens} of {config['max_tokens']}",
            f"seconds {loop_elapsed} of {config['max_duration_seconds']}",
        ]
        if approval_consumed:
            return _blocked(
                f"{reason} The scoped approval was consumed, but the same checkpoint is still unresolved; repair it before retrying.",
                loop_warnings,
            ) | {"loop": loop}
        return _approval(reason, loop_warnings) | {"loop": loop}
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
    approval_override = state.get("approval_override")
    override_scope = approval_override.get("scope", {}) if isinstance(approval_override, dict) else {}
    bypass_approval = (
        isinstance(approval_override, dict)
        and approval_override.get("gate") == current_role
        and isinstance(override_scope, dict)
        and "resume_workflow" in override_scope.get("actions", [])
    )
    if bypass_approval:
        state.pop("approval_override", None)
        warnings.append("Consumed one scoped approval override for this checkpoint.")
    grants = state.get("approval_grants", [])
    valid_grants = [item for item in grants if isinstance(item, dict)] if isinstance(grants, list) else []
    active_verifier_acceptance = False
    verifier_artifacts = {
        "architecture-consistency-agent": "architecture_consistency.json",
        "semantic-conflict-agent": "semantic_conflict.json",
        "reviewer": "review.json",
    }
    verifier_artifact = verifier_artifacts.get(current_role)
    if (
        bypass_approval
        and verifier_artifact
        and "accept_unavailable_verification" in override_scope.get("actions", [])
        and verifier_environment_unavailable(artifacts_dir, verifier_artifact)
    ):
        active_verifier_acceptance = True
        current_fingerprint = verifier_artifact_fingerprint(
            artifacts_dir,
            verifier_artifact,
        )
        override_scope["verifier_fingerprint"] = current_fingerprint
        approval_id = approval_override.get("approval_id")
        for grant in valid_grants:
            if grant.get("approval_id") != approval_id or grant.get("gate") != current_role:
                continue
            scope = grant.get("scope")
            if isinstance(scope, dict):
                scope["verifier_fingerprint"] = current_fingerprint
    risk_approved = any(
        item.get("gate") == "risk-classifier"
        and isinstance(item.get("scope"), dict)
        and "patch_high_risk" in item["scope"].get("actions", [])
        for item in valid_grants
    )
    security_approved = security_acceptance_granted(state, artifacts_dir)
    security_artifact = _artifact(artifacts_dir, "security.json")
    accept_security_override = (
        bypass_approval
        and "accept_security_finding" in override_scope.get("actions", [])
        and isinstance(security_artifact, dict)
        and scope_accepts_security(override_scope, security_artifact)
    )
    invalid_security_acceptance = (
        bypass_approval
        and "accept_security_finding" in override_scope.get("actions", [])
        and not accept_security_override
    )
    budget_approved = any(
        isinstance(item.get("scope"), dict)
        and "resume_workflow" in item["scope"].get("actions", [])
        and "budget exceeded" in str(item.get("reason", "")).lower()
        for item in valid_grants
    )

    if advisory and advisory not in {"continue", current_role}:
        warnings.append(f"Ignored advisory next_action={advisory!r}; deterministic routing is authoritative.")

    security = security_blockers(state, artifacts_dir)
    severity = security_severity(state, artifacts_dir)
    if security and severity == "critical":
        return _blocked("A CRITICAL security finding blocks the workflow.", warnings + security)

    risk = _artifact(artifacts_dir, "risk.json")
    risk_class = risk.get("risk_class") if isinstance(risk, dict) else state.get("risk_class")
    if risk_class == "high" and risk_approved and current_role == "orchestrator":
        return _blocked(
            "HIGH risk implementation passed verification, but publication is forbidden by policy.",
            warnings,
        )
    if risk_class == "high" and not (bypass_approval or risk_approved):
        return _approval("Risk class is HIGH. Publication is not allowed without human approval.", warnings)

    if security and (
        invalid_security_acceptance or not (bypass_approval or security_approved)
    ):
        return _approval(
            f"A {severity.upper()} security finding requires human approval.",
            warnings + security,
        )

    if (
        security
        and current_role == "security-agent"
        and bypass_approval
        and not accept_security_override
    ):
        return _repair_route(
            "security_repair",
            state=state,
            role_result=result,
            artifacts_dir=artifacts_dir,
            routing=routing,
            approval_consumed=bypass_approval,
        )

    budget_blockers = _budget_blockers(state, workflows)
    if budget_blockers and not (bypass_approval or budget_approved):
        return _approval("Workflow budget exceeded; execution is awaiting approval.", warnings + budget_blockers)
    if execution_mode(state) == "adaptive":
        plan = execution_plan(state)
        if plan:
            usage = BudgetUsage.from_state(state)
            decision = BudgetController.from_plan(plan).assess(usage, mandatory_role=True)
            state["budget_usage"] = usage.as_dict()
            state["budget_action"] = decision.as_dict()
            if decision.action == BudgetAction.REQUIRE_APPROVAL and not (
                bypass_approval or budget_approved
            ):
                return _approval(
                    "Adaptive task budget exhausted; execution is awaiting approval.",
                    warnings + [
                        decision.reason,
                        "exhausted dimensions: " + ", ".join(decision.exhausted_dimensions),
                    ],
                )

    if result.get("status") == "awaiting_approval":
        return _approval(
            str(result.get("summary", f"Role {current_role} did not complete successfully.")),
            warnings + _list_values(result.get("blockers")),
        )
    if result.get("status") in {"blocked", "failed"}:
        return _blocked(
            str(result.get("summary", f"Role {current_role} did not complete successfully.")),
            warnings + _list_values(result.get("blockers")),
        )

    quality = quality_status(state, artifacts_dir)
    if current_role == "quality-runner" and quality == "fail":
        if ci_status(state, artifacts_dir) == "fail":
            route = _repair_route(
                "ci_repair",
                state=state,
                role_result=result,
                artifacts_dir=artifacts_dir,
                routing=routing,
                approval_consumed=bypass_approval,
            )
        else:
            route = _repair_route(
                "quality_repair",
                state=state,
                role_result=result,
                artifacts_dir=artifacts_dir,
                routing=routing,
                approval_consumed=bypass_approval,
            )
        if not route["stop"]:
            after_loop_budget = _budget_blockers(state, workflows)
            if after_loop_budget:
                return _approval("Workflow repair budget exceeded; execution is awaiting approval.", after_loop_budget) | {"loop": route.get("loop")}
            if ci_status(state, artifacts_dir) == "fail":
                route["next_role"] = "ci-repair-agent"
                route["reason"] = "CI checks failed; starting bounded CI repair loop."
        return route
    if current_role == "reviewer":
        reviewer_status = review_status(state, artifacts_dir)
        reviewer_environment_unavailable = verifier_environment_unavailable(
            artifacts_dir, "review.json"
        )
        reviewer_unavailability_accepted = (
            active_verifier_acceptance
            or verifier_unavailability_accepted(state, artifacts_dir, current_role)
        )
        if reviewer_environment_unavailable:
            if not reviewer_unavailability_accepted:
                return _approval(
                    "reviewer could not verify the environment; independent review acceptance is required.",
                    warnings + ["Repair the environment or explicitly accept unavailable independent verification."],
                )
            warnings.append(
                "Accepted unavailable independent verification from reviewer; publication must remain draft."
            )
        elif reviewer_status == "block":
            route = _repair_route(
                "review_repair",
                state=state,
                role_result=result,
                artifacts_dir=artifacts_dir,
                routing=routing,
                approval_consumed=bypass_approval,
            )
            if not route["stop"]:
                after_loop_budget = _budget_blockers(state, workflows)
                if after_loop_budget:
                    return _approval("Workflow repair budget exceeded; execution is awaiting approval.", after_loop_budget) | {"loop": route.get("loop")}
            return route
    if current_role == "frontend-qa-agent":
        frontend_verdict = verifier_verdict(artifacts_dir, "frontend_qa.json")
        if frontend_verdict == "broken":
            route = _repair_route(
                "frontend_verification_repair",
                state=state,
                role_result=result,
                artifacts_dir=artifacts_dir,
                routing=routing,
                approval_consumed=bypass_approval,
            )
            if not route["stop"]:
                after_loop_budget = _budget_blockers(state, workflows)
                if after_loop_budget:
                    return _approval(
                        "Frontend repair budget exceeded; execution is awaiting approval.",
                        after_loop_budget,
                    ) | {"loop": route.get("loop")}
            return route
        if frontend_verdict == "unavailable":
            warnings.append("Frontend verification is unavailable; any publication must remain draft.")
    if current_role in {"architecture-consistency-agent", "semantic-conflict-agent"}:
        artifact_name = (
            "architecture_consistency.json"
            if current_role == "architecture-consistency-agent"
            else "semantic_conflict.json"
        )
        verification = verifier_verdict(artifacts_dir, artifact_name)
        unavailability_accepted = (
            active_verifier_acceptance
            or verifier_unavailability_accepted(state, artifacts_dir, current_role)
        )
        if verification == "broken":
            if verifier_environment_unavailable(artifacts_dir, artifact_name):
                if not unavailability_accepted:
                    return _approval(
                        f"{current_role} could not verify the environment; implementation will not be repeated.",
                        warnings + ["Repair the environment or explicitly accept unavailable independent verification."],
                    )
                warnings.append(
                    f"Accepted unavailable independent verification from {current_role}; publication must remain draft."
                )
            else:
                return _repair_route(
                    "review_repair",
                    state=state,
                    role_result=result,
                    artifacts_dir=artifacts_dir,
                    routing=routing,
                    approval_consumed=bypass_approval,
                )
        if verification == "unavailable":
            if not unavailability_accepted:
                return _approval(f"{current_role} is unavailable; independent code verification is required.", warnings)
            warnings.append(
                f"Accepted unavailable independent verification from {current_role}; publication must remain draft."
            )
    if current_role == "ci-repair-agent":
        return _route("quality-runner", "CI repair completed; quality must be re-run.", warnings=warnings)

    verdict = _artifact(artifacts_dir, "verdict.json")
    if (
        current_role == "orchestrator"
        and isinstance(verdict, dict)
        and verdict.get("decision") == "local_complete"
        and verdict.get("execution_status") == "completed"
        and verdict.get("checks_passed") is True
        and not _blocker_values(verdict)
    ):
        return _route(
            "",
            "Local work is complete; publication remains outside the authorized scope.",
            warnings=warnings + _list_values(verdict.get("warnings")),
        )

    blockers = workflow_blockers(state, result, artifacts_dir, current_role)
    if blockers:
        return _approval("Workflow blockers are present; execution is awaiting approval.", warnings + blockers)

    code = code_changed(state, artifacts_dir)
    ui = ui_changed(state, artifacts_dir)
    mode = execution_mode(state)
    next_role = ""
    reason = ""
    if mode == "adaptive" and current_role not in {
        "orchestrator",
        "publication-prepare",
        "publication",
    }:
        planned = adaptive_next_role(state, current_role=current_role)
        if planned is None:
            return _approval(
                "Adaptive execution plan has no ready node before final orchestration.",
                warnings + ["The DAG may be incomplete or its dependencies may be unsatisfied."],
            )
        next_role, reason = planned
        return _route(next_role, reason, warnings=warnings)
    if current_role == "issue-intake":
        next_role, reason = "context-compiler", "Issue intake is recorded; compile scoped context."
    elif current_role == "context-compiler":
        if mode == "fast":
            next_role, reason = "implementation-agent", "Fast deterministic context is ready; implement the narrow task."
        else:
            next_role, reason = "planner", "Context is available; planning is the next required gate."
    elif current_role == "planner":
        next_role, reason = "risk-classifier", "Planner output is advisory; risk classification is the next required gate."
    elif current_role == "risk-classifier":
        if state.get("fast_escalation_reasons") and "implementation-agent" in completed_roles(state):
            if code:
                next_role, reason = "test-generator", "Escalated code implementation already exists; continue test verification."
            else:
                next_role, reason = "quality-runner", "Escalated non-code implementation already exists; continue deterministic verification."
        else:
            next_role, reason = "implementation-agent", "Risk is below HIGH; implementation is the next required gate."
    elif current_role == "implementation-agent":
        if mode == "fast":
            escalation = fast_path_blockers(state, artifacts_dir)
            if escalation:
                state["effective_mode"] = "full"
                state["fast_escalation_reasons"] = escalation
                next_role, reason = "planner", "Fast-path limits were exceeded; continue with the full verification chain."
                warnings.extend(escalation)
            else:
                next_role, reason = "quality-runner", "Fast implementation is bounded; run deterministic quality checks."
        elif code:
            next_role, reason = "test-generator", "Implementation completed; test generation is the next required gate."
        else:
            next_role, reason = "quality-runner", "No code files changed; run deterministic quality checks."
    elif current_role == "test-generator":
        next_role, reason = "quality-runner", "Tests are recorded; quality checks are required."
    elif current_role == "quality-runner":
        next_role, reason = "security-agent", "Quality passed; security is the next required gate."
    elif current_role == "security-agent":
        if mode == "fast":
            next_role, reason = "reviewer", "Fast security checks passed; independent review is next."
        elif ui:
            next_role, reason = "frontend-qa-agent", "User-visible or UI files changed; visual evidence gate is required."
        elif architecture_review_required(state, artifacts_dir):
            next_role, reason = "architecture-consistency-agent", "Code changed; architecture consistency gate is required."
        elif semantic_review_required(state, artifacts_dir):
            next_role, reason = "semantic-conflict-agent", "Risk-bearing domain code changed; semantic conflict review is required."
        else:
            next_role, reason = "reviewer", "No optional UI or code-impacting gate is required; review is next."
    elif current_role == "frontend-qa-agent":
        if architecture_review_required(state, artifacts_dir):
            next_role, reason = "architecture-consistency-agent", "Frontend evidence is recorded; code-impacting architecture gate is required."
        elif semantic_review_required(state, artifacts_dir):
            next_role, reason = "semantic-conflict-agent", "Frontend evidence is recorded; semantic conflict review is required."
        else:
            next_role, reason = "reviewer", "Frontend evidence is recorded; review is next."
    elif current_role == "architecture-consistency-agent":
        if semantic_review_required(state, artifacts_dir):
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
        visual_present = isinstance(visual, dict) and (
            visual.get("verdict") == "works" or visual.get("evidence_collected") is True
        )
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
    "changed_line_count",
    "code_changed",
    "decide_next_role",
    "diff_hash",
    "failure_fingerprint",
    "load_yaml",
    "required_gate_roles",
    "reviewer_requires_llm",
    "security_blockers",
    "ui_changed",
    "workflow_blockers",
]

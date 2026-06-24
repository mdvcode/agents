#!/usr/bin/env python3
"""Create scoped role context manifests for agent workflow runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / ".agents" / "skills"
ROLE_SKILLS = {
    "issue-intake": ["issue-intake", "repo-policy", "context-engineering"],
    "context-compiler": ["context-engineering", "repo-policy"],
    "planner": ["context-engineering", "repo-policy", "structured-output-guard"],
    "risk-classifier": ["repo-policy", "security-checklist"],
    "implementation-agent": ["repo-policy", "python-standards", "test-writing"],
    "test-generator": ["test-writing", "structured-output-guard"],
    "quality-runner": ["structured-output-guard"],
    "security-agent": ["security-checklist", "repo-policy"],
    "frontend-qa-agent": ["context-engineering"],
    "architecture-consistency-agent": ["repo-policy", "context-engineering"],
    "semantic-conflict-agent": ["repo-policy", "structured-output-guard"],
    "reviewer": ["repo-policy", "structured-output-guard"],
    "ci-repair-agent": ["repo-policy", "test-writing"],
    "orchestrator": ["repo-policy", "git-workflow", "structured-output-guard"],
    "eval-runner": ["structured-output-guard"],
    "report-agent": ["structured-output-guard"],
    "publication": ["git-workflow", "release-safety", "repo-policy"],
}


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def safe_relative(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path.resolve())


def artifact_references(artifacts_dir: Path) -> list[dict[str, str]]:
    if not artifacts_dir.exists():
        return []
    refs: list[dict[str, str]] = []
    for path in sorted(artifacts_dir.iterdir()):
        if not path.is_file():
            continue
        if path.name.endswith((".json", ".md", ".txt", ".py")):
            refs.append({"path": str(path), "kind": path.suffix.lstrip(".") or "file"})
    return refs


def skill_references(role: str) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for skill_name in ROLE_SKILLS.get(role, []):
        path = SKILLS / skill_name / "SKILL.md"
        if path.exists():
            refs.append({"name": skill_name, "path": str(path.resolve())})
    return refs


def create_context_manifest(
    *,
    run_id: str,
    role: str,
    goal: str,
    repository: Path,
    artifacts_dir: Path,
    context_dir: Path,
    project: str,
    project_profile: str,
    token_budget: int,
    allowed_tools: Sequence[str],
    previous_roles: Sequence[str],
) -> Path:
    manifest = {
        "run_id": run_id,
        "role": role,
        "goal": goal,
        "repository": str(repository.resolve()),
        "artifacts_dir": str(artifacts_dir.resolve()),
        "project": project,
        "project_profile": project_profile,
        "token_budget": token_budget,
        "allowed_tools": list(allowed_tools),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "context_files": [
            {"path": str((ROOT / "AGENTS.md").resolve()), "kind": "policy"},
            {"path": str((ROOT / ".agent-policy.yaml").resolve()), "kind": "policy"},
            {"path": str((ROOT / ".agent-project-profiles.yaml").resolve()), "kind": "profile"},
            {"path": str((ROOT / ".agent-repositories.yaml").resolve()), "kind": "registry"},
        ],
        "artifact_references": artifact_references(artifacts_dir),
        "skill_references": skill_references(role),
        "previous_roles": list(previous_roles),
        "retrieval_rules": [
            "Read only the listed context files and artifacts needed for this role.",
            "Use repository search for exact symbols or paths before opening broad files.",
            "Keep raw command outputs outside the context manifest.",
            "Write role outputs as strict JSON matching schemas/role_result.schema.json.",
        ],
        "raw_outputs_dir": str((context_dir.parent / "raw").resolve()),
    }
    path = context_dir / f"{role}.json"
    write_json(path, manifest)
    return path


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--artifacts-dir", type=Path, required=True)
    parser.add_argument("--context-dir", type=Path, required=True)
    parser.add_argument("--project", default="")
    parser.add_argument("--project-profile", default="")
    parser.add_argument("--token-budget", type=int, default=12000)
    args = parser.parse_args()
    path = create_context_manifest(
        run_id=args.run_id,
        role=args.role,
        goal=args.goal,
        repository=args.repository,
        artifacts_dir=args.artifacts_dir,
        context_dir=args.context_dir,
        project=args.project,
        project_profile=args.project_profile,
        token_budget=args.token_budget,
        allowed_tools=["filesystem_read", "repository_search"],
        previous_roles=[],
    )
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

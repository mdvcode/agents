#!/usr/bin/env python3
"""Create scoped role context manifests for agent workflow runs."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_harness.context import ContextEngine
from ai_harness.context.sources import ROLE_SKILLS


MEMORY_CONTROL_ROOT = ROOT
SKILLS = ROOT / ".agents" / "skills"
ROLE_CAPABILITIES = ROOT / ".agent-role-capabilities.yaml"
ROLE_CONTRACTS = ROOT / ".agent-role-contracts.yaml"
DEFAULT_MAX_TOTAL_CONTEXT_BYTES = 120000
DEFAULT_MAX_FILE_CONTEXT_BYTES = 24000


def load_yaml_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    return data if isinstance(data, dict) else {}


def role_capability(role: str) -> dict[str, Any]:
    data = load_yaml_mapping(ROLE_CAPABILITIES)
    default = data.get("default", {})
    roles = data.get("roles", {})
    capability = dict(default if isinstance(default, dict) else {})
    if isinstance(roles, dict) and isinstance(roles.get(role), dict):
        capability.update(roles[role])
    return capability


def role_contract(role: str) -> dict[str, Any]:
    data = load_yaml_mapping(ROLE_CONTRACTS)
    default = data.get("default", {})
    roles = data.get("roles", {})
    contract = dict(default if isinstance(default, dict) else {})
    if isinstance(roles, dict) and isinstance(roles.get(role), dict):
        contract.update(roles[role])
    contract.setdefault("expected_artifacts", [])
    contract.setdefault("artifact_schemas", {})
    contract.setdefault("owned_artifact_patterns", [])
    contract.setdefault("execution_kind", "llm_role")
    contract.setdefault("llm_invocation", True)
    return contract


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


def role_skill_names(role: str, project_profile: str) -> list[str]:
    names = list(ROLE_SKILLS.get(role, ()))
    if project_profile == "nextjs_web" and role in {"implementation-agent", "ci-repair-agent"}:
        names = [name for name in names if name != "python-standards"]
    return names


def skill_references(role: str, project_profile: str) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for skill_name in role_skill_names(role, project_profile):
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
    filesystem_access: str = "",
    prompt_path: str = "",
    output_contract: str = "",
    expected_artifacts: Sequence[str] = (),
    runtime: str = "codex-cli",
) -> Path:
    capability = role_capability(role)
    contract = role_contract(role)
    tools = list(allowed_tools) or list(capability.get("tools", []))
    filesystem = filesystem_access or str(capability.get("filesystem", "read_only"))
    prompt = prompt_path or str(contract.get("prompt_path", ""))
    contract_path = output_contract or str(contract.get("output_contract", ""))
    artifacts = list(expected_artifacts) or list(contract.get("expected_artifacts", []))
    retrieval_query = " ".join(part for part in (goal.strip(), role.replace("-", " ")) if part)
    context_log_path = context_dir / "logs" / f"{role}.jsonl"
    engine = ContextEngine.default(
        control_root=MEMORY_CONTROL_ROOT,
        project=project,
        project_profile=project_profile,
        artifacts_dir=artifacts_dir,
        context_log_path=context_log_path,
        repository=repository,
        token_budget=token_budget,
    )
    context = engine.build(goal, repository, role, runtime)
    package_path = context_dir / "packages" / f"{role}.md"
    package_path.parent.mkdir(parents=True, exist_ok=True)
    package_path.write_text(context.package, encoding="utf-8")
    selected_context = list(context.log.get("selected", []))
    excluded_context = list(context.log.get("excluded", []))
    candidate_paths = sorted(
        {
            f"{item.get('source', '')}:{item.get('path', '')}"
            for item in selected_context + excluded_context
            if isinstance(item, dict)
        }
    )
    context_files = [
        {"path": str(package_path.resolve()), "kind": "context_package"},
    ]
    manifest = {
        "run_id": run_id,
        "role": role,
        "goal": goal,
        "repository": str(repository.resolve()),
        "artifacts_dir": str(artifacts_dir.resolve()),
        "project": project,
        "project_profile": project_profile,
        "token_budget": token_budget,
        "allowed_tools": tools,
        "filesystem_access": filesystem,
        "prompt_path": prompt,
        "output_contract": contract_path,
        "expected_artifacts": artifacts,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "context_budget": {
            "max_total_bytes": DEFAULT_MAX_TOTAL_CONTEXT_BYTES,
            "max_file_bytes": DEFAULT_MAX_FILE_CONTEXT_BYTES,
            "max_total_tokens": context.token_budget,
            "used_tokens": context.tokens_used,
        },
        "selected_context": selected_context,
        "excluded_context": excluded_context,
        "retrieval_queries": [retrieval_query] if retrieval_query else [],
        "source_file_candidates": candidate_paths,
        "repo_intelligence": {
            "context_engine": {
                "algorithm": str(context.log.get("retriever", "rule_based_keyword_v1")),
                "status": "compiled",
                "candidate_count": len(candidate_paths),
                "selected_count": len(selected_context),
                "excluded_count": len(excluded_context),
            }
        },
        "context_engine_version": 1,
        "context_package_path": str(package_path.resolve()),
        "context_log_path": context.log_path,
        "context_files": context_files,
        "artifact_references": artifact_references(artifacts_dir),
        "skill_references": skill_references(role, project_profile),
        "previous_roles": list(previous_roles),
        "retrieval_rules": [
            "Use the compiled Context Package as the only supplied knowledge input.",
            "Do not read Obsidian or other knowledge roots directly.",
            "Use repository search for exact symbols or paths before opening broad files.",
            "Keep raw command outputs outside the context manifest.",
            "Write role outputs as strict JSON matching schemas/role_result.schema.json.",
        ],
        "raw_outputs_dir": str((context_dir.parent / "raw-events").resolve()),
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

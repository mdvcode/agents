#!/usr/bin/env python3
"""Create scoped role context manifests for agent workflow runs."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ai_harness.context import ContextEngine
from ai_harness.context.sources import ROLE_SKILLS
from ai_harness.attachments.runtime import compile_attachment_context


MEMORY_CONTROL_ROOT = ROOT
SKILLS = ROOT / ".agents" / "skills"
ROLE_CAPABILITIES = ROOT / ".agent-role-capabilities.yaml"
ROLE_CONTRACTS = ROOT / ".agent-role-contracts.yaml"
DEFAULT_MAX_TOTAL_CONTEXT_BYTES = 120000
DEFAULT_MAX_FILE_CONTEXT_BYTES = 24000


def run_attachment_context(run_id: str, context_dir: Path) -> dict[str, Any] | None:
    """Compile optional worker-pinned run inputs without accepting any alternate path."""

    manifest_path = os.environ.get("AGENT_INPUT_MANIFEST", "").strip()
    manifest_sha256 = os.environ.get("AGENT_INPUT_MANIFEST_SHA256", "").strip()
    raw_count = os.environ.get("AGENT_ATTACHMENT_COUNT", "").strip()
    raw_consent = os.environ.get("AGENT_ATTACHMENT_RUNTIME_CONSENT", "").strip()
    if not any((manifest_path, manifest_sha256, raw_count, raw_consent)):
        return None
    if not all((manifest_path, manifest_sha256, raw_count, raw_consent)):
        raise ValueError("attachment runtime context metadata is incomplete")
    if raw_consent != "1":
        raise ValueError("attachment runtime context consent is missing")
    try:
        expected_count = int(raw_count)
    except ValueError as exc:
        raise ValueError("attachment runtime context count is invalid") from exc
    absolute_context_dir = Path(os.path.abspath(os.fspath(context_dir)))
    if absolute_context_dir.name != "context-manifests":
        raise ValueError("attachment context directory is not authoritative")
    return compile_attachment_context(
        run_root=absolute_context_dir.parent,
        manifest_path=manifest_path,
        manifest_sha256=manifest_sha256,
        runtime_consent=True,
        expected_count=expected_count,
        expected_run_id=run_id,
    )


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


def artifact_snapshot(artifacts_dir: Path) -> dict[str, str]:
    if not artifacts_dir.is_dir():
        return {}
    values: dict[str, str] = {}
    for path in sorted(artifacts_dir.iterdir()):
        if path.is_file() and not path.is_symlink():
            try:
                values[path.name] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
    return values


def incremental_runtime_delta(
    *,
    role: str,
    artifacts_dir: Path,
    context_dir: Path,
    previous_roles: Sequence[str],
) -> tuple[dict[str, Any], dict[str, str]]:
    current = artifact_snapshot(artifacts_dir)
    previous_manifest: dict[str, Any] = {}
    previous_role = ""
    for candidate in reversed(previous_roles):
        path = context_dir / f"{candidate}.json"
        value = load_yaml_mapping(path) if path.is_file() else {}
        if value:
            previous_manifest = value
            previous_role = candidate
            break
    previous = previous_manifest.get("artifact_snapshot", {})
    if not isinstance(previous, dict):
        previous = {}
    changed = sorted(
        name
        for name in set(previous) | set(current)
        if previous.get(name) != current.get(name)
    )
    errors_path = artifacts_dir.parent / "errors.jsonl"
    error_lines = []
    if errors_path.is_file():
        try:
            error_lines = [line for line in errors_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        except OSError:
            error_lines = []
    previous_error_count = int(previous_manifest.get("runtime_delta", {}).get("error_count", 0) or 0) if isinstance(previous_manifest.get("runtime_delta"), dict) else 0
    workflow_path = artifacts_dir.parent / "workflow.json"
    workflow = load_yaml_mapping(workflow_path) if workflow_path.is_file() else {}
    delta = {
        "from_role": previous_role,
        "to_role": role,
        "changed_artifacts": changed,
        "new_failures": error_lines[previous_error_count:],
        "decisions": {
            key: workflow.get(key)
            for key in ("last_route", "budget_action", "attention", "failure_kind")
            if workflow.get(key) not in (None, "", {}, [])
        },
        "error_count": len(error_lines),
    }
    return delta, current


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


def inspection_counts(items: Sequence[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(
        sorted(
            Counter(str(item.get(field, "unknown")) for item in items).items()
        )
    )


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
    project_key: str = "",
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
    runtime_delta, current_artifact_snapshot = incremental_runtime_delta(
        role=role,
        artifacts_dir=artifacts_dir,
        context_dir=context_dir,
        previous_roles=previous_roles,
    )
    runtime_delta_text = json.dumps(runtime_delta, indent=2, ensure_ascii=False, sort_keys=True)
    cache_query_salt = hashlib.sha256(
        json.dumps(
            {"artifacts": current_artifact_snapshot, "delta": runtime_delta},
            sort_keys=True,
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    engine = ContextEngine.default(
        control_root=MEMORY_CONTROL_ROOT,
        project=project,
        project_profile=project_profile,
        project_key=project_key,
        artifacts_dir=artifacts_dir,
        context_log_path=context_log_path,
        repository=repository,
        token_budget=token_budget,
        runtime_delta=runtime_delta_text,
        cache_query_salt=cache_query_salt,
    )
    context = engine.build(goal, repository, role, runtime)
    package_path = context_dir / "packages" / f"{role}.md"
    package_path.parent.mkdir(parents=True, exist_ok=True)
    package_path.write_text(context.package, encoding="utf-8")
    selected_context = list(context.log.get("selected", []))
    excluded_context = list(context.log.get("excluded", []))
    context_revision = str(context.log.get("context_revision", ""))
    effective_context_digest = str(context.log.get("effective_context_digest", ""))
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
    base_sources = {"policies", "project_profile"}
    changed_artifacts = set(runtime_delta["changed_artifacts"])
    context_layers = {
        "base": [
            item
            for item in selected_context
            if item.get("source") in base_sources or item.get("path") == "execution-plan.json"
        ],
        "role": [
            item
            for item in selected_context
            if item.get("source") != "runtime_delta"
            and not (item.get("source") == "run_artifacts" and item.get("path") in changed_artifacts)
            and item.get("source") not in base_sources
        ],
        "delta": [
            item
            for item in selected_context
            if item.get("source") == "runtime_delta"
            or (item.get("source") == "run_artifacts" and item.get("path") in changed_artifacts)
        ],
    }
    attachment_context = run_attachment_context(run_id, context_dir)
    manifest = {
        "run_id": run_id,
        "role": role,
        "goal": goal,
        "repository": str(repository.resolve()),
        "artifacts_dir": str(artifacts_dir.resolve()),
        "project": project,
        **({"project_key": project_key} if project_key else {}),
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
        "context_engine_version": 2,
        "context_revision": context_revision,
        "effective_context_digest": effective_context_digest,
        "context_inspector": {
            "context_revision": context_revision,
            "effective_context_digest": effective_context_digest,
            "runtime_destination": runtime,
            "included": selected_context,
            "excluded": excluded_context,
            "summary": {
                "included_count": len(selected_context),
                "excluded_count": len(excluded_context),
                "used_tokens": context.tokens_used,
                "token_budget": context.token_budget,
                "privacy": inspection_counts(selected_context + excluded_context, "privacy"),
                "trust": inspection_counts(selected_context + excluded_context, "trust"),
                "exclusion_reasons": inspection_counts(excluded_context, "reason_code"),
            },
        },
        "context_cache": dict(context.log.get("cache", {})),
        "context_layers": context_layers,
        "artifact_snapshot": current_artifact_snapshot,
        "runtime_delta": runtime_delta,
        "deduplication": dict(context.log.get("deduplication", {})),
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
    if attachment_context is not None:
        manifest["attachment_context"] = attachment_context
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
    parser.add_argument("--project-key", default="")
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
        project_key=args.project_key,
        project_profile=args.project_profile,
        token_budget=args.token_budget,
        allowed_tools=["filesystem_read", "repository_search"],
        previous_roles=[],
    )
    print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

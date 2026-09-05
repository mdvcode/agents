"""Read-only source previews and verified effective-input inspection."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ai_harness.context import ContextEngine
from ai_harness.context.content_guard import (
    ContextGuardError,
    redact_value,
    require_safe,
)
from ai_harness.context.payload import confined_path, read_snapshot
from ai_harness.project import default_config, load_project_config
from context_compiler import role_contract
from runtimes.registry import load_runtime_config


DIGEST_NAME = re.compile(r"[a-f0-9]{64}")


def preview_sources(
    *, repository: Path, goal: str, role: str, control_root: Path
) -> dict[str, Any]:
    if not goal.strip() or len(goal) > 20_000:
        raise ContextGuardError("Describe the task using at most 20,000 characters")
    require_safe(goal, "Task")
    if not re.fullmatch(r"[a-z][a-z-]{0,63}", role):
        raise ContextGuardError("Invalid context role")
    contract = role_contract(role)
    if not contract.get("prompt_path"):
        raise ContextGuardError("No model-backed context is configured for this role")
    config = (
        load_project_config(repository)
        if (repository / ".agent/project.yaml").is_file()
        else default_config(repository)
    )
    runtime = str(load_runtime_config()["provider"])
    engine = ContextEngine.default(
        control_root=control_root,
        project=config.project_id,
        project_profile=config.profile,
        repository=repository,
        token_budget=12_000,
    )
    # A draft inspection never starts workers or mutates run/repository state.
    engine.cache = None
    context = engine.build(goal, repository, role, runtime)
    return redact_value(
        {
            "scope": "draft_sources",
            "status": "preview",
            "role": role,
            "runtime": runtime,
            "context_revision": context.log["context_revision"],
            "context_package_digest": context.log["effective_context_digest"],
            "included": context.log["selected"],
            "excluded": context.log["excluded"],
            "tokens": context.tokens_used,
            "token_budget": context.token_budget,
            "package": context.package,
            "limitations": [
                "Draft source preview on the current checkout. The actual stage adds its role instructions, run artifacts, answers and execution settings; inspect its frozen input in Tasks."
            ],
        }
    )


def list_inputs(run_dir: Path) -> dict[str, Any]:
    directory = confined_path(run_dir, "context-manifests", "effective")
    records: list[dict[str, Any]] = []
    if directory.is_dir():
        for path in sorted(directory.glob("*.json")):
            if not DIGEST_NAME.fullmatch(path.stem):
                continue
            # Never expose a corrupted or legacy payload as verified input.
            value = read_snapshot(path)
            records.append(
                {
                    key: value[key]
                    for key in (
                        "effective_context_digest",
                        "prompt_digest",
                        "created_at",
                        "role",
                        "phase",
                        "status",
                        "scope",
                    )
                }
                | {"runtime": value["payload"]["runtime"]}
            )
    records.sort(key=lambda item: item["created_at"], reverse=True)
    return {"run_id": run_dir.name, "inputs": records[:200]}


def get_input(run_dir: Path, digest: str) -> dict[str, Any]:
    if not DIGEST_NAME.fullmatch(digest):
        raise ContextGuardError("Invalid context digest")
    return read_snapshot(
        confined_path(run_dir, "context-manifests", "effective", digest + ".json")
    )

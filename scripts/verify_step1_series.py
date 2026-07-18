#!/usr/bin/env python3
"""Verify 10-20 real task runs as the Step 1 acceptance gate."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


REQUIRED_LOW_MEDIUM_ROLES = {
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
    "publication",
}
REQUIRED_HIGH_ROLES = {"issue-intake", "context-compiler", "planner", "risk-classifier", "approval-gate"}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
)


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: top-level value must be an object")
    return value


def completed_roles(state: dict[str, Any]) -> set[str]:
    roles: set[str] = set()
    for checkpoint in state.get("roles", []):
        if not isinstance(checkpoint, dict):
            continue
        result = checkpoint.get("result", {})
        if isinstance(result, dict) and result.get("status") in {"completed", "awaiting_approval"}:
            roles.add(str(checkpoint.get("role", "")))
    return roles


def structured_errors(path: Path) -> bool:
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            return False
        if not isinstance(entry, dict) or not {"stage", "code", "message"}.issubset(entry):
            return False
    return True


def secret_findings(run_dir: Path) -> list[str]:
    findings: list[str] = []
    roots = (run_dir / "artifacts", run_dir / "raw-events", run_dir / "role-results")
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if any(pattern.search(text) for pattern in SECRET_PATTERNS):
                findings.append(str(path.relative_to(run_dir)))
    return findings


def raw_codex_proof(run_dir: Path) -> bool:
    planner_events = run_dir / "raw-events" / "planner.jsonl"
    if not planner_events.exists():
        return False
    text = planner_events.read_text(encoding="utf-8")
    return '"type":"thread.started"' in text or '"type": "thread.started"' in text


def verify_run(run_dir: Path) -> dict[str, Any]:
    blockers: list[str] = []
    try:
        state = read_json(run_dir / "workflow.json")
        risk = read_json(run_dir / "artifacts" / "risk.json")
        metrics = read_json(run_dir / "metrics.json")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return {"run_id": run_dir.name, "status": "fail", "risk_class": "", "blockers": [str(exc)]}
    risk_class = str(risk.get("risk_class", ""))
    roles = completed_roles(state)
    executor = state.get("executor", {})
    real_executor = (
        isinstance(executor, dict)
        and executor.get("kind") == "codex_cli"
        and executor.get("production") is True
        and "codex_cli_executor.py" in str(executor.get("command", ""))
        and raw_codex_proof(run_dir)
    )
    if not real_executor:
        blockers.append("run does not contain real Codex executor proof")
    if int(metrics.get("tokens_used", 0) or 0) <= 0:
        blockers.append("token usage was not persisted")
    before = str(state.get("base_branch_sha_before", ""))
    after = str(state.get("base_branch_sha_after", ""))
    if not before or before != after:
        blockers.append("default branch SHA changed or was not recorded")
    publication_path = run_dir / "artifacts" / "publication.json"
    publication = read_json(publication_path) if publication_path.exists() else None
    if risk_class in {"low", "medium"}:
        missing = sorted(REQUIRED_LOW_MEDIUM_ROLES - roles)
        if missing:
            blockers.append("missing required gates: " + ", ".join(missing))
        if not isinstance(publication, dict):
            blockers.append("LOW/MEDIUM run has no publication state")
        elif not (
            publication.get("execution_status") == "completed"
            and publication.get("pr_created_or_updated") is True
            and publication.get("pr_url")
        ):
            blockers.append("LOW/MEDIUM run did not reach a PR")
    elif risk_class == "high":
        missing = sorted(REQUIRED_HIGH_ROLES - roles)
        if missing:
            blockers.append("HIGH run missing stop gates: " + ", ".join(missing))
        if state.get("execution_status") != "awaiting_approval":
            blockers.append("HIGH run did not stop for approval")
        if isinstance(publication, dict) and any(
            publication.get(field) is True
            for field in ("commit_created", "branch_pushed", "pr_created_or_updated")
        ):
            blockers.append("HIGH run performed a publication mutation")
    else:
        blockers.append(f"invalid risk class: {risk_class!r}")
    terminal = state.get("execution_status") in {"blocked", "failed", "awaiting_approval"}
    if terminal and not structured_errors(run_dir / "errors.jsonl"):
        blockers.append("terminal failure is not persisted as structured state")
    leaks = secret_findings(run_dir)
    if leaks:
        blockers.append("possible secret leakage: " + ", ".join(leaks))
    return {
        "run_id": run_dir.name,
        "status": "pass" if not blockers else "fail",
        "risk_class": risk_class,
        "input_fingerprint": str(state.get("input_fingerprint", "")),
        "pr_url": str(publication.get("pr_url", "")) if isinstance(publication, dict) else "",
        "commit_sha": str(publication.get("commit_sha", "")) if isinstance(publication, dict) else "",
        "real_executor": real_executor,
        "structured_failure": terminal and structured_errors(run_dir / "errors.jsonl"),
        "secret_leaks": leaks,
        "blockers": blockers,
    }


def verify_series(
    runs_dir: Path,
    minimum_tasks: int = 10,
    run_ids: list[str] | None = None,
) -> dict[str, Any]:
    selected = [runs_dir / run_id for run_id in run_ids] if run_ids is not None else list(runs_dir.iterdir()) if runs_dir.exists() else []
    results = [
        verify_run(path)
        for path in sorted(selected)
        if path.is_dir() and (path / "workflow.json").exists()
    ]
    fingerprints: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        fingerprint = str(result.get("input_fingerprint", ""))
        if fingerprint:
            fingerprints.setdefault(fingerprint, []).append(result)
    duplicate_publications: list[str] = []
    for fingerprint, entries in fingerprints.items():
        pr_urls = {str(entry.get("pr_url", "")) for entry in entries if entry.get("pr_url")}
        commit_shas = {str(entry.get("commit_sha", "")) for entry in entries if entry.get("commit_sha")}
        if len(pr_urls) > 1 or len(commit_shas) > 1:
            duplicate_publications.append(fingerprint)
    blockers = [
        f"{result['run_id']}: {message}"
        for result in results
        for message in result.get("blockers", [])
    ]
    if minimum_tasks < 10:
        blockers.append("Step 1 minimum_tasks cannot be lower than 10")
    low_medium = sum(result.get("risk_class") in {"low", "medium"} for result in results)
    high = sum(result.get("risk_class") == "high" for result in results)
    if len(results) < minimum_tasks:
        blockers.append(f"only {len(results)} task runs found; minimum is {minimum_tasks}")
    if len(results) > 20:
        blockers.append(f"{len(results)} task runs selected; Step 1 evidence series must contain at most 20")
    if low_medium == 0:
        blockers.append("series has no LOW/MEDIUM publication run")
    if high == 0:
        blockers.append("series has no HIGH stop run")
    if duplicate_publications:
        blockers.append("duplicate publication detected")
    default_branch_mutations = [
        result["run_id"]
        for result in results
        if any("default branch SHA" in blocker for blocker in result.get("blockers", []))
    ]
    leaks = [
        f"{result['run_id']}:{path}"
        for result in results
        for path in result.get("secret_leaks", [])
    ]
    return {
        "status": "pass" if not blockers else "fail",
        "minimum_tasks": minimum_tasks,
        "task_count": len(results),
        "real_executor_runs": sum(result.get("real_executor") is True for result in results),
        "low_medium_published": low_medium,
        "high_stopped": high,
        "duplicate_publications": duplicate_publications,
        "default_branch_mutations": default_branch_mutations,
        "secret_leaks": leaks,
        "structured_failures": sum(result.get("structured_failure") is True for result in results),
        "runs": results,
        "blockers": blockers,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, default=Path(".agent-runs"))
    parser.add_argument("--minimum-tasks", type=int, default=10)
    parser.add_argument("--manifest", type=Path, default=None, help="Newline-separated run ids to verify.")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_ids = None
    if args.manifest is not None:
        run_ids = [line.strip() for line in args.manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = verify_series(args.runs_dir, args.minimum_tasks, run_ids)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

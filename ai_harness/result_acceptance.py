"""Human feedback on a concrete result; never execution or publication authority."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_harness.context.content_guard import ContextGuardError, redact_text, require_safe
from ai_harness.project import safe_branch


STATUSES = frozenset({"accepted", "needs_changes", "rejected"})
HISTORY_FILE = "result-acceptance.jsonl"
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_HISTORY_BYTES = 4 * 1024 * 1024
MAX_HISTORY_RECORDS = 1000
MAX_GIT_BYTES = 16 * 1024 * 1024
EVIDENCE_FILES = (
    "verdict.json", "implementation.json", "quality.json", "security.json", "review.json",
    "test_plan.json", "test_result.json", "frontend_qa.json", "architecture_consistency.json",
    "semantic_conflict.json", "change_set.json", "publication.json",
)


class ResultAcceptanceError(ValueError):
    """Invalid feedback or unavailable result evidence."""


class StaleResultError(ResultAcceptanceError):
    """The displayed result changed before feedback was saved."""


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _read_bytes(path: Path, limit: int) -> bytes:
    if path.is_symlink():
        raise ResultAcceptanceError("Result evidence must not be a symbolic link")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ResultAcceptanceError("Result evidence must be a regular file")
            data = source.read(limit + 1)
    except OSError as exc:
        raise ResultAcceptanceError("Result evidence is unavailable") from exc
    if len(data) > limit:
        raise ResultAcceptanceError("Result evidence exceeds the inspection limit")
    return data


def _object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(_read_bytes(path, MAX_JSON_BYTES))
    except (ValueError, UnicodeError) as exc:
        raise ResultAcceptanceError("Result evidence is missing or malformed") from exc
    if not isinstance(value, dict):
        raise ResultAcceptanceError("Result evidence must be a JSON object")
    return value


def _git(checkout: Path, *arguments: str) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", *arguments], cwd=checkout,
            capture_output=True, check=False, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ResultAcceptanceError("Result Git evidence is unavailable") from exc
    if result.returncode or len(result.stdout) > MAX_GIT_BYTES:
        raise ResultAcceptanceError("Result Git evidence is unavailable or exceeds the inspection limit")
    return result.stdout


def _git_evidence(workflow: dict[str, Any]) -> dict[str, Any]:
    raw_checkout = workflow.get("checkout_path") or workflow.get("worktree")
    branch = workflow.get("task_branch") or workflow.get("branch")
    base = workflow.get("base_sha")
    if not isinstance(raw_checkout, str) or not raw_checkout or not isinstance(branch, str) or not safe_branch(branch):
        raise ResultAcceptanceError("The run has no reviewable checkout and branch identity")
    if not isinstance(base, str) or not re.fullmatch(r"[0-9a-f]{40,64}", base):
        raise ResultAcceptanceError("The run has no recorded base commit")
    try:
        checkout = Path(raw_checkout).resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ResultAcceptanceError("The run's checkout path is invalid") from exc
    top = Path(os.fsdecode(_git(checkout, "rev-parse", "--show-toplevel")).strip()).resolve()
    current_branch = os.fsdecode(_git(checkout, "symbolic-ref", "--quiet", "--short", "HEAD")).strip()
    if top != checkout or current_branch != branch:
        raise ResultAcceptanceError("The checkout no longer contains this run's task branch")
    head = _git(checkout, "rev-parse", "--verify", "HEAD").decode("ascii").strip()
    _git(checkout, "cat-file", "-e", f"{base}^{{commit}}")
    diff = _git(checkout, "diff", "--no-ext-diff", "--no-textconv", "--binary", base, "--")
    changed = _git(checkout, "diff", "--no-ext-diff", "--no-textconv", "--name-only", "-z", base, "--")
    untracked = _git(checkout, "ls-files", "--others", "--exclude-standard", "-z")
    names = sorted(set(os.fsdecode(name) for name in (changed + untracked).split(b"\0") if name))
    if len(names) > 1000:
        raise ResultAcceptanceError("Too many changed files to inspect this result")
    files: dict[str, Any] = {}
    total = 0
    for name in names:
        path = checkout / name
        if not path.resolve().is_relative_to(checkout):
            raise ResultAcceptanceError("A result file points outside its checkout")
        if not path.exists() and not path.is_symlink():
            files[name] = {"kind": "deleted"}
            continue
        # Identity describes final content, independent of staging or committing it.
        if path.is_symlink():
            data = os.fsencode(os.readlink(path))
            mode = "symlink"
        else:
            data = _read_bytes(path, MAX_GIT_BYTES)
            mode = "executable" if path.stat().st_mode & 0o111 else "file"
        total += len(data)
        if total > MAX_GIT_BYTES:
            raise ResultAcceptanceError("Changed result files exceed the inspection limit")
        files[name] = {"kind": mode, "sha256": hashlib.sha256(data).hexdigest()}
    return {
        "checkout_path": str(checkout), "branch": branch, "base_sha": base, "head_sha": head,
        "diff_sha256": hashlib.sha256(diff).hexdigest(), "content_sha256": _digest(files),
        "changed_files": names,
    }


def _publication_url(checkout: Path, publication: dict[str, Any]) -> str | None:
    url = publication.get("pr_url")
    if not isinstance(url, str):
        return None
    match = re.fullmatch(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/[1-9][0-9]*", url)
    if not match:
        return None
    try:
        remote = _git(checkout, "remote", "get-url", "origin").decode().strip()
    except (ResultAcceptanceError, UnicodeError):
        return None
    origin = re.fullmatch(r"(?:https://github\.com/|git@github\.com:)([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?", remote)
    return url if origin and origin[1].casefold() == match[1].casefold() else None


def _summary(workflow: dict[str, Any]) -> str:
    roles = workflow.get("roles", [])
    if isinstance(roles, list):
        for role in reversed(roles):
            if not isinstance(role, dict) or role.get("role") != "implementation-agent":
                continue
            result = role.get("result")
            if isinstance(result, dict) and isinstance(result.get("summary"), str):
                return redact_text(result["summary"])[:1500]
    return "Completed result. Inspect the changed files and recorded checks before assessing it."


def _snapshot(run_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if run_dir.is_symlink() or not run_dir.is_dir() or (run_dir / "artifacts").is_symlink():
        raise ResultAcceptanceError("The result must belong to a real run directory")
    workflow = _object(run_dir / "workflow.json")
    if workflow.get("run_id") != run_dir.name or workflow.get("execution_status") != "completed":
        raise ResultAcceptanceError("Only a completed run can receive result feedback")
    evidence = {
        name: _object(run_dir / "artifacts" / name)
        for name in EVIDENCE_FILES if (run_dir / "artifacts" / name).exists()
    }
    verdict = evidence.get("verdict.json", {})
    if verdict.get("execution_status") != "completed" or not isinstance(verdict.get("decision"), str) or verdict["decision"] not in {
        "local_complete", "publish_pr", "no_changes",
    }:
        raise ResultAcceptanceError("A completed reviewable verdict is required")
    if any(name not in evidence for name in ("quality.json", "security.json", "review.json")):
        raise ResultAcceptanceError("The result's required check and review evidence is missing")
    git = _git_evidence(workflow)
    artifact_fingerprint = _digest({"workflow": workflow, "artifacts": evidence})
    fingerprint = _digest({"run_evidence": artifact_fingerprint, "base_sha": git["base_sha"], "content_sha256": git["content_sha256"]})
    checks = {
        name: value if isinstance(value, str) and value in {"pass", "warn", "fail", "not_run", "works", "broken", "unavailable"} else "unknown"
        for name, value in (
            ("quality", evidence["quality.json"].get("overall_status")),
            ("security", evidence["security.json"].get("verdict")),
            ("review", evidence["review.json"].get("verdict")),
        )
    }
    check_statuses = {
        "pass": "pass", "works": "pass", "fail": "fail", "broken": "fail",
        "warn": "warn", "unavailable": "unavailable", "not_run": "not_run",
    }
    return {
        "result_fingerprint": fingerprint, "run_evidence_fingerprint": artifact_fingerprint, "decision": verdict["decision"],
        "checks": checks,
        "checks_passed": verdict.get("checks_passed") is True,
        "evidence": {
            "summary": _summary(workflow),
            "goal": redact_text(workflow.get("goal", ""))[:3000] if isinstance(workflow.get("goal"), str) else "",
            "verification_kind": "deterministic_baseline" if evidence["review.json"].get("verification_kind") == "deterministic_baseline" else "recorded_review",
            "changed_files": [redact_text(name)[:500] for name in git["changed_files"][:100]],
            "changed_files_total": len(git["changed_files"]),
            "checks": [{"name": name, "status": check_statuses.get(value, "unknown")} for name, value in checks.items()],
            "publication_url": _publication_url(Path(git["checkout_path"]), evidence.get("publication.json", {})),
            "artifacts": [f"artifacts/{name}" for name in evidence],
            "checkout_path": git["checkout_path"], "branch": git["branch"],
            "head_sha": git["head_sha"], "base_sha": git["base_sha"],
            "diff_sha256": git["diff_sha256"], "content_sha256": git["content_sha256"],
        },
    }, workflow


def _history(data: bytes, run_id: str) -> list[dict[str, Any]]:
    if len(data) > MAX_HISTORY_BYTES:
        raise ResultAcceptanceError("Result feedback history exceeds its limit")
    records: list[dict[str, Any]] = []
    for line in data.splitlines():
        try:
            item = json.loads(line)
        except (ValueError, UnicodeError) as exc:
            raise ResultAcceptanceError("Result feedback history is malformed") from exc
        if not isinstance(item, dict):
            raise ResultAcceptanceError("Result feedback history is malformed")
        digest = item.get("record_fingerprint")
        payload = {key: value for key, value in item.items() if key != "record_fingerprint"}
        if (
            item.get("schema_version") != 1 or item.get("run_id") != run_id
            or item.get("sequence") != len(records) + 1
            or not isinstance(item.get("status"), str) or item["status"] not in STATUSES
            or not isinstance(item.get("result_fingerprint"), str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["result_fingerprint"])
            or not isinstance(item.get("reason"), str) or len(item["reason"]) > 2000
            or not isinstance(item.get("actor"), str) or not 1 <= len(item["actor"]) <= 120
            or not isinstance(item.get("recorded_at"), str)
            or item.get("previous_record") != (records[-1]["record_fingerprint"] if records else "")
            or digest != _digest(payload)
        ):
            raise ResultAcceptanceError("Result feedback history failed integrity validation")
        snapshot = item.get("snapshot")
        if snapshot is not None and (
            not isinstance(snapshot, dict) or snapshot.get("result_fingerprint") != item["result_fingerprint"]
            or not isinstance(snapshot.get("evidence"), dict) or not isinstance(snapshot.get("checks"), dict)
        ):
            raise ResultAcceptanceError("Result feedback snapshot is malformed")
        records.append(item)
    if len(records) > MAX_HISTORY_RECORDS:
        raise ResultAcceptanceError("Result feedback history exceeds its limit")
    return records


def _load_history(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / HISTORY_FILE
    if not path.exists() and not path.is_symlink():
        return []
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise ResultAcceptanceError("Result feedback history must be a regular file")
            fcntl.flock(source.fileno(), fcntl.LOCK_SH)
            return _history(source.read(MAX_HISTORY_BYTES + 1), run_dir.name)
    except OSError as exc:
        raise ResultAcceptanceError("Result feedback history is unavailable") from exc


def result_acceptance(run_dir: Path) -> dict[str, Any]:
    """Bounded projection; unavailable evidence is explicit, never an accepted result."""

    base: dict[str, Any] = {
        "schema_version": 1, "eligible": False, "reason": "", "result_fingerprint": "",
        "current": None, "historical": None, "archived_result": None, "history_count": 0, "stale": False, "checks": {}, "evidence": {},
    }
    try:
        if run_dir.is_symlink():
            raise ResultAcceptanceError("The result must belong to a real run directory")
        history = _load_history(run_dir)
        base["history_count"] = len(history)
        if history and isinstance(history[-1].get("snapshot"), dict):
            base["historical"] = {"assessment": history[-1], "snapshot": history[-1]["snapshot"], "availability": "unavailable"}
        snapshot, _ = _snapshot(run_dir)
        base.update(snapshot, eligible=True)
        if history:
            latest = history[-1]
            if latest["result_fingerprint"] == snapshot["result_fingerprint"]:
                base["current"] = latest
                if base["historical"]:
                    base["historical"]["availability"] = "current"
            else:
                base["stale"] = True
                if base["historical"]:
                    base["historical"]["availability"] = "superseded"
    except ResultAcceptanceError as exc:
        base["reason"] = str(exc)
        base["stale"] = base["history_count"] > 0
        try:
            if (run_dir / "direct-baseline.json").is_file() and baseline_receipt(run_dir, base)["valid"]:
                stored = _object(run_dir / "direct-baseline.json")["snapshot"]
                base.update(archived_result=stored, evidence=stored["evidence"], checks=stored["checks"])
        except (ResultAcceptanceError, KeyError, TypeError):
            pass
    return base


def _text(value: Any, field: str, limit: int, *, required: bool = True) -> str:
    if not isinstance(value, str) or len(value) > limit or "\0" in value:
        raise ResultAcceptanceError(f"{field} must be text of at most {limit} characters")
    value = value.strip()
    if required and not value:
        raise ResultAcceptanceError(f"{field} is required")
    try:
        require_safe(value, f"Result feedback {field}")
    except ContextGuardError as exc:
        raise ResultAcceptanceError(str(exc)) from exc
    return value


def record_result_acceptance(
    run_dir: Path, *, status: str, reason: str, actor: str, expected_fingerprint: str,
) -> dict[str, Any]:
    """Append feedback once, checking the exact version displayed to the human."""

    if not isinstance(status, str) or status not in STATUSES:
        raise ResultAcceptanceError("status must be accepted, needs_changes or rejected")
    reason = _text(reason, "reason", 2000, required=status != "accepted")
    actor = _text(actor, "actor", 120)
    snapshot, _ = _snapshot(run_dir)
    if expected_fingerprint != snapshot["result_fingerprint"]:
        raise StaleResultError("The result changed; refresh it before recording feedback")
    path = run_dir / HISTORY_FILE
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(descriptor, "r+b") as output:
            fcntl.flock(output.fileno(), fcntl.LOCK_EX)
            if not stat.S_ISREG(os.fstat(output.fileno()).st_mode):
                raise ResultAcceptanceError("Result feedback history must be a regular file")
            history = _history(output.read(MAX_HISTORY_BYTES + 1), run_dir.name)
            fresh, _ = _snapshot(run_dir)
            if fresh["result_fingerprint"] != expected_fingerprint:
                raise StaleResultError("The result changed; refresh it before recording feedback")
            if history and all(history[-1].get(key) == value for key, value in {
                "status": status, "reason": reason, "actor": actor,
                "result_fingerprint": expected_fingerprint,
            }.items()):
                return _accepted_projection(fresh, history)
            record = {
                "schema_version": 1, "run_id": run_dir.name, "sequence": len(history) + 1,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "status": status, "reason": reason, "actor": actor,
                "result_fingerprint": expected_fingerprint,
                "snapshot": fresh,
                "previous_record": history[-1]["record_fingerprint"] if history else "",
            }
            record["record_fingerprint"] = _digest(record)
            encoded = (json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n").encode()
            if len(history) >= MAX_HISTORY_RECORDS or output.tell() + len(encoded) > MAX_HISTORY_BYTES:
                raise ResultAcceptanceError("Result feedback history exceeds its limit")
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
            return _accepted_projection(fresh, [*history, record])
    except OSError as exc:
        raise ResultAcceptanceError("Could not persist result feedback") from exc


def _accepted_projection(snapshot: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1, "eligible": True, "reason": "", **snapshot,
        "current": history[-1], "history_count": len(history), "stale": False,
        "historical": {"assessment": history[-1], "snapshot": history[-1].get("snapshot", snapshot), "availability": "current"},
    }


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return float(value) if math.isfinite(value) and value >= 0 else None
    except OverflowError:
        return None


BASELINE_FILES = (
    "workflow.json", "metrics.json", "runtime-preflight.json", "role-requests/implementation-agent.json",
    "role-results/implementation-agent.json", "raw-events/implementation-agent.json", "raw-events/implementation-agent.jsonl", "raw-events/sdk-events.jsonl",
    "artifacts/implementation.json", "artifacts/quality.json", "artifacts/security.json", "artifacts/review.json", "artifacts/verdict.json",
)



def baseline_tokens(run_dir: Path) -> int | None:
    """Only SDK turn usage is comparable; the model's claimed count is not evidence."""
    try:
        result = _object(run_dir / "role-results/implementation-agent.json")
        if result.get("output_repair_attempts"):
            # Current repair streams do not record their own usage. Do not
            # undercount those turns using only the first turn's breakdown.
            return None
        event = _object(run_dir / "raw-events/implementation-agent.jsonl")
        usage = event.get("usage")
        if event.get("provider") != "codex-sdk" or event.get("status") != "completed" or not isinstance(usage, dict):
            return None
        counts = [usage.get(name) for name in ("input_tokens", "cached_input_tokens", "output_tokens")]
        if not all(isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**12 for value in counts):
            return None
        inputs, cached, outputs = counts
        if cached > inputs:
            return None
        total = inputs - cached + outputs
        # The current adapter also uses all-zero fields when SDK usage is absent.
        return total if total > 0 else None
    except ResultAcceptanceError:
        return None

def baseline_receipt(run_dir: Path, acceptance: dict[str, Any]) -> dict[str, Any]:
    """Validate locally collected Runtime evidence; no self-reported metric import."""

    try:
        receipt = _object(run_dir / "direct-baseline.json")
        stored = receipt.get("snapshot")
        if (
            not isinstance(stored, dict) or receipt.get("snapshot_fingerprint") != _digest(stored)
            or stored.get("result_fingerprint") != receipt.get("result_fingerprint")
            or not isinstance(stored.get("evidence"), dict) or not isinstance(stored.get("checks"), dict)
        ):
            raise ResultAcceptanceError("The baseline snapshot is missing or malformed")
        snapshot = acceptance if acceptance.get("eligible") else stored
        hashes = receipt.get("file_sha256")
        if (
            receipt.get("schema_version") != 1 or receipt.get("kind") != "direct_sdk_baseline"
            or receipt.get("collector") != "run_direct_baseline_v1" or receipt.get("run_id") != run_dir.name
            or receipt.get("provider") != "codex-sdk" or receipt.get("runtime_invocations") != 1
            or not snapshot.get("result_fingerprint") or receipt.get("result_fingerprint") != snapshot["result_fingerprint"]
            or not isinstance(hashes, dict) or set(hashes) != set(BASELINE_FILES)
        ):
            raise ResultAcceptanceError("Baseline receipt does not identify this collected result")
        for name in BASELINE_FILES:
            if hashlib.sha256(_read_bytes(run_dir / name, MAX_GIT_BYTES)).hexdigest() != hashes[name]:
                raise ResultAcceptanceError("Baseline evidence changed after collection")
        workflow = _object(run_dir / "workflow.json")
        preflight = _object(run_dir / "runtime-preflight.json")
        request = _object(run_dir / "role-requests/implementation-agent.json")
        result = _object(run_dir / "role-results/implementation-agent.json")
        metrics = _object(run_dir / "metrics.json")
        roles = workflow.get("roles")
        raw = _object(run_dir / "raw-events/implementation-agent.json")
        output = json.loads(raw.get("stdout", ""))
        events = [json.loads(line) for line in _read_bytes(run_dir / "raw-events/sdk-events.jsonl", MAX_GIT_BYTES).splitlines()]
        runtime = workflow.get("runtime", {})
        if (
            workflow.get("workflow") != "direct_sdk_baseline" or workflow.get("effective_mode") != "direct_codex"
            or workflow.get("execution_status") != "completed" or not isinstance(runtime, dict)
            or runtime.get("provider") != "codex-sdk" or runtime.get("production") is not True
            or runtime.get("api_required") is not False or preflight.get("execution_status") != "completed"
            or request.get("run_id") != run_dir.name or request.get("role") != "implementation-agent"
            or result.get("status") != "completed" or result.get("child_tasks")
            or not isinstance(roles, list) or len(roles) != 1 or not isinstance(roles[0], dict)
            or roles[0].get("role") != "implementation-agent" or roles[0].get("result") != result
            or workflow.get("loops") != {} or metrics.get("repair_attempts_per_task") != 0
            or metrics.get("tokens_used") != baseline_tokens(run_dir) or workflow.get("tokens_used") != baseline_tokens(run_dir)
            or raw.get("provider") != "codex-sdk" or raw.get("role") != "implementation-agent" or raw.get("returncode") != 0
            or not isinstance(output, dict) or output.get("status") != "completed"
            or any(result.get(key) != value for key, value in output.items())
            or not events or not all(isinstance(event, dict) and isinstance(event.get("method"), str) for event in events)
            or not any(event["method"] == "turn/completed" for event in events)
            or _object(run_dir / "artifacts" / "review.json").get("verification_kind") != "deterministic_baseline"
        ):
            raise ResultAcceptanceError("Baseline Runtime or verification evidence is incomplete")
        return {"valid": True, "label": "Codex SDK · один проход", "reason": "", "availability": "current" if acceptance.get("eligible") else "historical_snapshot"}
    except (ResultAcceptanceError, ValueError, TypeError, UnicodeError) as exc:
        return {"valid": False, "label": "Codex SDK · один проход", "reason": str(exc)}


def result_observation(run_dir: Path, workflow: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    """Descriptive measures without substituting workflow success for human acceptance."""

    acceptance = copy.deepcopy(result_acceptance(run_dir))
    # Original task text belongs to the protected result endpoint, not aggregate metrics.
    snapshots = [acceptance, acceptance.get("archived_result") or {}, (acceptance.get("current") or {}).get("snapshot", {})]
    historical = acceptance.get("historical") or {}
    snapshots.extend([historical.get("snapshot", {}), historical.get("assessment", {}).get("snapshot", {})])
    for snapshot in snapshots:
        snapshot.get("evidence", {}).pop("goal", None)
    repairs = _number(metrics.get("repair_attempts_per_task"))
    if repairs is None and isinstance(workflow.get("loops"), dict):
        iterations = [_number(loop.get("iterations")) for loop in workflow["loops"].values() if isinstance(loop, dict)]
        if len(iterations) == len(workflow["loops"]) and all(value is not None for value in iterations):
            repairs = sum(value for value in iterations if value is not None)
    # Approval requests are not interventions. Only a recorded human response counts.
    attention = workflow.get("attention_history")
    # The producer retains only its latest 50 attention entries. Do not present
    # a potentially truncated history as the complete count.
    answers = None
    if isinstance(attention, list) and len(attention) < 50 and all(isinstance(item, dict) for item in attention):
        answers = sum(item.get("resolution") == "answer_recorded" for item in attention)
    approvals: int | None = None
    approval_path = run_dir / "raw-events" / "approvals.jsonl"
    if approval_path.exists():
        try:
            events = [json.loads(line) for line in _read_bytes(approval_path, MAX_JSON_BYTES).splitlines()]
            if all(isinstance(item, dict) and isinstance(item.get("event"), str) for item in events):
                approvals = sum(item.get("event") in {"approval.approved", "approval.rejected"} for item in events)
        except (ResultAcceptanceError, ValueError, UnicodeError):
            pass
    comparison_fields = {
        "repository": workflow.get("repository"), "goal": workflow.get("goal"),
        "base_sha": workflow.get("base_sha"), "project_profile": workflow.get("project_profile"),
    }
    pair_key = ""
    if all(isinstance(value, str) and value for value in comparison_fields.values()):
        comparison_fields["attachments"] = workflow.get("input_manifest_sha256", "")
        pair_key = _digest(comparison_fields)
    return {
        "run_id": run_dir.name, "mode": workflow.get("effective_mode"),
        "status": workflow.get("execution_status"), "acceptance": acceptance,
        "baseline": baseline_receipt(run_dir, acceptance) if workflow.get("effective_mode") == "direct_codex" else None,
        "pair_key": pair_key,
        "measurements": {
            "elapsed_seconds": _number(workflow.get("elapsed_seconds")),
            "tokens_used": _number(metrics.get("tokens_used", workflow.get("tokens_used"))),
            "automatic_repairs": repairs,
            "user_interventions": answers + approvals if answers is not None and approvals is not None else None,
        },
    }


def _cohort(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ratings = Counter(
        item["acceptance"]["current"]["status"] for item in rows if item["acceptance"].get("current")
    )
    rated = sum(ratings.values())
    historical = Counter(
        row["acceptance"]["historical"]["assessment"]["status"] for row in rows
        if row["acceptance"].get("historical") and not row["acceptance"].get("current")
    )
    measures = {}
    for name in ("elapsed_seconds", "tokens_used", "automatic_repairs", "user_interventions"):
        values = sorted(value for row in rows if (value := _number(row["measurements"].get(name))) is not None)
        measures[name] = {
            "samples": len(values), "missing": len(rows) - len(values),
            "mean": round(sum(values) / len(values), 3) if values else None,
            "median": (values[(len(values) - 1) // 2] + values[len(values) // 2]) / 2 if values else None,
        }
    return {
        "runs": len(rows), "rated": rated, "unrated": len(rows) - rated - sum(historical.values()),
        "current_unrated": len(rows) - rated,
        "accepted": ratings["accepted"], "needs_changes": ratings["needs_changes"], "rejected": ratings["rejected"],
        "acceptance_rate": round(ratings["accepted"] / rated, 6) if rated else None,
        "rating_coverage": round(rated / len(rows), 6) if rows else None,
        "stale_ratings": sum(row["acceptance"]["stale"] for row in rows),
        "historical_only": {"rated": sum(historical.values()), **{status: historical[status] for status in sorted(STATUSES)}},
        "measurements": measures,
    }


def _paired_comparison(cohorts: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    groups: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for mode, rows in cohorts.items():
        for row in rows:
            if row["pair_key"]:
                groups[row["pair_key"]][mode].append(row)
    paired = {mode: [] for mode in cohorts}
    compatible = 0
    ambiguous = 0
    rated_pairs = 0
    for group in groups.values():
        if not all(mode in group for mode in cohorts):
            continue
        if any(len(group[mode]) != 1 for mode in cohorts):
            ambiguous += 1
            continue
        compatible += 1
        if all(group[mode][0]["acceptance"].get("current") for mode in cohorts):
            rated_pairs += 1
            for mode in cohorts:
                paired[mode].append(group[mode][0])
    return {
        "compatible_pairs": compatible, "rated_pairs": rated_pairs, "ambiguous_groups": ambiguous,
        "modes": {mode: _cohort(rows) for mode, rows in paired.items()},
        "matching": "exact repository, goal, base commit, project profile and attachment digest",
        "limitation": "Matched inputs do not equalize model versions, context, budgets or task order; no causal superiority claim.",
    }


def compare_results(observations: list[dict[str, Any]]) -> dict[str, Any]:
    """Matched inputs are descriptive evidence, not a benchmark win or rollout gate."""

    cohorts = {mode: [row for row in observations if row["mode"] == mode] for mode in ("fast", "full")}
    direct_attempts = [row for row in observations if row["mode"] == "direct_codex"]
    direct = [row for row in direct_attempts if (row.get("baseline") or {}).get("valid") is True]
    return {
        "schema_version": 1, "kind": "human_result_feedback", "winner": None,
        "denominator": "current explicit human ratings only; unreviewed and stale ratings are excluded",
        "measurement_population": "all recorded runs in each effective mode; missing measures remain unknown",
        "modes": {mode: _cohort(rows) for mode, rows in cohorts.items()},
        "excluded_unknown_or_other_mode": len(observations) - sum(map(len, cohorts.values())) - len(direct),
        "paired": _paired_comparison(cohorts),
        "paired_with_sdk": {mode: _paired_comparison({mode: rows, "direct_codex": direct}) for mode, rows in cohorts.items()},
        "direct_codex": {
            "status": "available" if direct else "not_available", "label": "Codex SDK · один проход",
            **_cohort(direct), "historical_measurements": sum(row["baseline"].get("availability") == "historical_snapshot" for row in direct), "attempts": len(direct_attempts), "excluded_unverified": len(direct_attempts) - len(direct),
            "reason": "Collected through Runtime with deterministic checks; desktop chat remains unmeasured." if direct else "No validated local SDK baseline receipt is available; desktop chat remains unmeasured.",
            "limitation": "Completed checked samples only; inspect excluded attempts separately. No independent model review, no causal superiority claim.",
        },
        "acceptance_gate_changed": False,
    }

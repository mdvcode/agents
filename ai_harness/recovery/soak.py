"""Strict manifest and report contracts for the production runtime soak gate."""

from __future__ import annotations

from typing import Any


MINIMUM_SCENARIO_COUNTS = {
    "successful": 20,
    "runtime_timeout": 3,
    "invalid_output": 2,
    "quality_repair": 2,
    "approval": 1,
    "process_kill": 1,
    "unrecoverable": 1,
}
REQUIRED_INVARIANTS = {
    "worker_service_survived": True,
    "recoverable_tasks_recovered": True,
    "unrecoverable_task_dead_lettered": True,
    "publication_probe_complete": True,
    "run_identity_preserved": True,
    "no_hanging_leases": True,
}


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def validate_soak_manifest(value: object) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict) or value.get("version") != 1:
        return ["soak manifest must be a version 1 object"]
    tasks = value.get("tasks")
    if not isinstance(tasks, list):
        return ["soak manifest tasks must be a list"]
    if len(tasks) < sum(MINIMUM_SCENARIO_COUNTS.values()):
        errors.append("soak manifest must contain at least 30 tasks")
    counts = {category: 0 for category in MINIMUM_SCENARIO_COUNTS}
    keys: set[str] = set()
    for index, item in enumerate(tasks):
        if not isinstance(item, dict):
            errors.append(f"tasks[{index}] must be an object")
            continue
        key = item.get("task_key")
        category = item.get("category")
        payload = item.get("payload")
        if not isinstance(key, str) or not key:
            errors.append(f"tasks[{index}].task_key is required")
        elif key in keys:
            errors.append(f"duplicate task_key: {key}")
        else:
            keys.add(key)
        if category not in counts:
            errors.append(f"tasks[{index}].category is unsupported")
        else:
            counts[str(category)] += 1
        if not isinstance(payload, dict) or not all(
            isinstance(payload.get(field), str) and payload[field]
            for field in ("task_id", "repository")
        ):
            errors.append(f"tasks[{index}].payload requires task_id and repository")
    for category, minimum in MINIMUM_SCENARIO_COUNTS.items():
        if counts[category] < minimum:
            errors.append(f"scenario {category} requires {minimum} tasks, got {counts[category]}")
    return errors


def validate_soak_report(value: object, *, minimum_duration_seconds: int = 7200) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict) or value.get("version") != 1:
        return ["soak report must be a version 1 object"]
    duration = _integer(value.get("duration_seconds"))
    if duration is None or duration < minimum_duration_seconds:
        errors.append(f"soak duration must be at least {minimum_duration_seconds} seconds")
    task_count = _integer(value.get("task_count"))
    if task_count is None or task_count < sum(MINIMUM_SCENARIO_COUNTS.values()):
        errors.append("soak report must cover at least 30 tasks")
    scenarios = value.get("scenario_counts")
    if not isinstance(scenarios, dict):
        errors.append("scenario_counts must be an object")
    else:
        for category, minimum in MINIMUM_SCENARIO_COUNTS.items():
            observed = _integer(scenarios.get(category))
            if observed is None or observed < minimum:
                errors.append(f"scenario {category} requires at least {minimum} results")
    invariants = value.get("invariants")
    if not isinstance(invariants, dict):
        errors.append("invariants must be an object")
    else:
        for name, expected in REQUIRED_INVARIANTS.items():
            if invariants.get(name) is not expected:
                errors.append(f"invariant {name} must be {expected}")
        for name in ("duplicate_commit_count", "duplicate_pr_count", "lost_run_count", "hanging_lease_count"):
            if _integer(invariants.get(name)) != 0:
                errors.append(f"invariant {name} must be zero")
        publication_runs = _integer(invariants.get("publication_runs"))
        if publication_runs is None or publication_runs < 1:
            errors.append("soak report must include at least one publication run")
    if value.get("timed_out") is not False:
        errors.append("soak run must finish without the collection timeout")
    return errors

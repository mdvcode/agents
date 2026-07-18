#!/usr/bin/env python3
"""List workflow and worker exceptions without reading full transcripts."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from task_queue import DEFAULT_DB, TaskQueue


ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / ".agent-runs"


@dataclass(frozen=True)
class RunException:
    source: str
    identifier: str
    status: str
    requires_human: bool
    stalled: bool
    reasons: list[str]
    run_id: str


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def error_reasons(path: Path) -> list[str]:
    if not path.exists():
        return []
    reasons: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            reasons.append("malformed structured error")
            continue
        if isinstance(entry, dict):
            code = str(entry.get("code", ""))
            message = str(entry.get("message", ""))
            if code or message:
                reasons.append(": ".join(value for value in (code, message) if value))
    return reasons


def run_exception(run_dir: Path, *, stale_seconds: int, now: float | None = None) -> RunException | None:
    workflow_path = run_dir / "workflow.json"
    if not workflow_path.exists():
        return None
    state = read_json(workflow_path)
    status = str(state.get("execution_status", "unknown"))
    reasons = [str(item) for item in state.get("blockers", []) if isinstance(item, (str, int))]
    reasons.extend(error_reasons(run_dir / "errors.jsonl"))
    security = read_json(run_dir / "artifacts" / "security.json")
    if security.get("verdict") == "broken" or security.get("status") == "fail":
        reasons.append("security gate failed")
    publication = read_json(run_dir / "artifacts" / "publication.json")
    verdict = read_json(run_dir / "artifacts" / "verdict.json")
    visual = verdict.get("visual_evidence", {})
    if (
        publication.get("pr_state") == "draft"
        and isinstance(visual, dict)
        and visual.get("required") is True
        and visual.get("provided") is not True
    ):
        reasons.append("PR draft requires evidence")
    current_time = now or time.time()
    stalled = status == "running" and current_time - workflow_path.stat().st_mtime > stale_seconds
    if stalled:
        reasons.append("workflow heartbeat is stale")
    requires_human = status in {"blocked", "failed", "awaiting_approval"} or stalled or bool(
        set(reasons) & {"security gate failed", "PR draft requires evidence"}
    )
    return RunException(
        source="run",
        identifier=run_dir.name,
        status=status,
        requires_human=requires_human,
        stalled=stalled,
        reasons=sorted(set(reasons)),
        run_id=run_dir.name,
    )


def queue_exceptions(queue: TaskQueue, *, stale_seconds: int) -> list[RunException]:
    stalled_ids = {record.id for record in queue.stalled(stale_seconds=stale_seconds)}
    entries: list[RunException] = []
    for record in queue.list():
        stalled = record.id in stalled_ids
        reasons = [value for value in (record.exception_reason, record.last_error) if value]
        if stalled:
            reasons.append("worker stalled")
        requires_human = record.requires_human or stalled or record.status == "dead_letter"
        entries.append(
            RunException(
                source="queue",
                identifier=str(record.id),
                status=record.status,
                requires_human=requires_human,
                stalled=stalled,
                reasons=sorted(set(reasons)),
                run_id=record.run_id,
            )
        )
    return entries


def collect(
    *,
    runs_dir: Path = RUNS_DIR,
    db_path: Path = DEFAULT_DB,
    status: str = "",
    requires_human: bool = False,
    stalled: bool = False,
    stale_seconds: int = 180,
) -> list[RunException]:
    entries = [
        entry
        for path in sorted(runs_dir.iterdir()) if runs_dir.exists() and path.is_dir()
        if (entry := run_exception(path, stale_seconds=stale_seconds)) is not None
    ] if runs_dir.exists() else []
    if db_path.exists():
        entries.extend(queue_exceptions(TaskQueue(db_path), stale_seconds=stale_seconds))
    if status:
        entries = [entry for entry in entries if entry.status == status]
    if requires_human:
        entries = [entry for entry in entries if entry.requires_human]
    if stalled:
        entries = [entry for entry in entries if entry.stalled]
    return sorted(entries, key=lambda entry: (entry.source, entry.identifier))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--requires-human", action="store_true")
    parser.add_argument("--status", default="")
    parser.add_argument("--stalled", action="store_true")
    parser.add_argument("--stale-seconds", type=int, default=180)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    entries = collect(
        runs_dir=args.runs_dir,
        db_path=args.db,
        status=args.status,
        requires_human=args.requires_human,
        stalled=args.stalled,
        stale_seconds=args.stale_seconds,
    )
    print(json.dumps([asdict(entry) for entry in entries], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

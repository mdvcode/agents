from __future__ import annotations

import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from task_queue import TaskQueue
from worker_service import WorkerService
from ai_harness.build import harness_build_fingerprint
from worker_pool import WorkerOutcome
import worker_pool


class EmptyPool:
    def run_wave(self) -> list[object]:
        return []


def test_build_fingerprint_is_layout_independent(tmp_path: Path) -> None:
    source = tmp_path / "source"
    installed = tmp_path / "installed-share"
    installed_package = tmp_path / "site-packages" / "ai_harness"
    (source / "ai_harness").mkdir(parents=True)
    (source / "scripts").mkdir()
    installed.mkdir()
    (installed / "scripts").mkdir()
    installed_package.mkdir(parents=True)
    source_assets = source / "ai_harness" / "observability" / "assets"
    installed_assets = installed_package / "observability" / "assets"
    source_assets.mkdir(parents=True)
    installed_assets.mkdir(parents=True)
    (source / "ai_harness" / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (installed_package / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source_assets / "tweebit-wordmark.svg").write_text("<svg/>\n", encoding="utf-8")
    (installed_assets / "tweebit-wordmark.svg").write_text("<svg/>\n", encoding="utf-8")
    (source / "scripts" / "worker.py").write_text("RUN = True\n", encoding="utf-8")
    (installed / "scripts" / "worker.py").write_text("RUN = True\n", encoding="utf-8")

    assert harness_build_fingerprint(source) == harness_build_fingerprint(
        installed, package_root=installed_package
    )


def test_build_fingerprint_covers_audited_observability_assets(tmp_path: Path) -> None:
    root = tmp_path / "harness"
    assets = root / "ai_harness" / "observability" / "assets"
    assets.mkdir(parents=True)
    wordmark = assets / "tweebit-wordmark.svg"
    wordmark.write_text("<svg><path d='M0 0'/></svg>\n", encoding="utf-8")
    initial = harness_build_fingerprint(root)

    wordmark.write_text("<svg><path d='M1 1'/></svg>\n", encoding="utf-8")

    assert harness_build_fingerprint(root) != initial


def test_build_fingerprint_covers_execution_policy_and_make_targets(tmp_path: Path) -> None:
    root = tmp_path / "harness"
    (root / "ai_harness").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / ".agent-role-policy.yaml").write_text("version: 1\n", encoding="utf-8")
    (root / "Makefile").write_text("check:\n\tpython3 -m pytest\n", encoding="utf-8")
    initial = harness_build_fingerprint(root)

    (root / "Makefile").write_text("check:\n\t.venv/bin/python -m pytest\n", encoding="utf-8")

    assert harness_build_fingerprint(root) != initial


def test_build_fingerprint_covers_bundled_skills_and_templates(tmp_path: Path) -> None:
    root = tmp_path / "harness"
    (root / "ai_harness").mkdir(parents=True)
    (root / "scripts").mkdir()
    skill = root / ".agents" / "skills" / "context-engineering" / "SKILL.md"
    template = root / "docs" / "templates" / "goal.md"
    skill.parent.mkdir(parents=True)
    template.parent.mkdir(parents=True)
    skill.write_text("version one\n", encoding="utf-8")
    template.write_text("goal version one\n", encoding="utf-8")
    initial = harness_build_fingerprint(root)

    skill.write_text("version two\n", encoding="utf-8")
    changed_skill = harness_build_fingerprint(root)
    template.write_text("goal version two\n", encoding="utf-8")
    changed_template = harness_build_fingerprint(root)

    assert changed_skill != initial
    assert changed_template != changed_skill


def test_worker_service_registers_reports_health_and_stops_gracefully(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    state_path = tmp_path / "service.json"
    service = WorkerService(
        queue=queue,
        service_id="service-test",
        workers=2,
        lease_seconds=30,
        heartbeat_seconds=1,
        poll_seconds=0.01,
        state_path=state_path,
    )
    service.pool = EmptyPool()  # type: ignore[assignment]
    service.register()

    health = service.health()
    result = service.serve(once=True)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    workers = [record for record in queue.list_workers() if record.service_id == "service-test"]

    assert health["status"] == "healthy"
    assert len(health["workers"]) == 2
    assert result == 0
    assert state["status"] == "stopped"
    assert len(state["build_fingerprint"]) == 64
    assert {record.status for record in workers} == {"stopped"}


def test_worker_service_writes_failures_next_to_its_state_file(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    state_path = tmp_path / "service.json"
    service = WorkerService(
        queue=queue,
        service_id="service-test",
        workers=1,
        lease_seconds=30,
        heartbeat_seconds=1,
        state_path=state_path,
    )

    service.write_service_error(RuntimeError("visible startup failure"))

    log_path = tmp_path / "worker-service.log"
    assert log_path.is_file()
    assert "visible startup failure" in log_path.read_text(encoding="utf-8")


def test_service_restart_reclaims_active_task_and_continues_same_run(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(worker_pool, "RUNS_DIR", tmp_path / ".agent-runs")
    queue = TaskQueue(tmp_path / "queue.db")
    queued = queue.enqueue(
        task_key="restart-active",
        payload={"task_id": "restart-active", "repository": str(tmp_path), "run_id": "run-restart"},
        run_id="run-restart",
    )
    old_service = WorkerService(
        queue=queue,
        service_id="restart-service",
        workers=1,
        lease_seconds=30,
        heartbeat_seconds=1,
        poll_seconds=0.01,
        state_path=tmp_path / "old-service.json",
    )
    old_service.register()
    claimed = queue.claim(worker_id="restart-service-1", lease_seconds=30)
    assert claimed is not None
    assert queue.mark_running(queued.id, "restart-service-1")
    assert queue.assign_run(queued.id, "restart-service-1", "run-restart")
    with queue.connect() as connection:
        connection.execute("UPDATE tasks SET lease_expires_at=? WHERE id=?", (time.time() - 1, queued.id))

    replacement = WorkerService(
        queue=queue,
        service_id="restart-service",
        workers=1,
        lease_seconds=30,
        heartbeat_seconds=1,
        poll_seconds=0.01,
        state_path=tmp_path / "replacement-service.json",
        restart_count=1,
    )
    replacement.pool.handler = lambda record, _worker: WorkerOutcome(status="completed", run_id=record.run_id)
    replacement.register()

    records = replacement.pool.run_wave()

    assert [record.status for record in records] == ["completed"]
    assert records[0].run_id == "run-restart"
    assert replacement.total_restart_count == 1
    assert any(event["event"] == "lease_expired_resume" for event in queue.events(queued.id))

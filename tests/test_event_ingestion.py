from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ai_harness.project import trust_key

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from event_ingestion import EventError, enqueue_envelope, normalize_event
from runtime_contracts import load_json, validate_contract
from task_queue import TaskQueue


@pytest.mark.parametrize("source", ["cli", "github_issue", "webhook", "api", "ci"])
def test_all_sources_normalize_to_one_task_envelope(tmp_path: Path, source: str) -> None:
    payload = {
        "external_id": f"external-{source}",
        "task_id": f"task-{source}",
        "goal": f"Handle {source}",
    }
    envelope = normalize_event(source=source, payload=payload, repository=tmp_path)

    assert envelope["source"] == source
    assert envelope["task_id"] == f"task-{source}"
    assert envelope["repository"] == str(tmp_path.resolve())
    assert envelope["workspace_mode"] == "worktree"
    assert envelope["checkout_path"] == str(tmp_path.resolve())
    assert envelope["task_branch"] == f"issue/task-{source}"
    assert envelope["base_sha"] == ""
    assert envelope["branch_owner_run_id"] == ""
    assert envelope["mode"] == "auto"
    assert envelope["event_id"]
    assert "input_manifest" not in envelope
    assert "input_manifest_sha256" not in envelope
    assert "attachment_count" not in envelope
    assert "attachment_runtime_consent" not in envelope


def test_event_delivery_is_idempotent_in_queue(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    envelope = normalize_event(
        source="github_issue",
        payload={"issue": {"id": 10, "number": 4, "title": "Fix it", "body": "Details"}},
        repository=tmp_path,
    )

    first = enqueue_envelope(queue, envelope)
    second = enqueue_envelope(queue, envelope)

    assert first.id == second.id
    assert first.payload["goal"] == "Fix it\n\nDetails"
    assert "input_manifest" not in first.payload
    assert len(list((tmp_path / "events").glob("*.json"))) == 1


@pytest.mark.parametrize("source", ["api", "github_issue", "webhook", "ci"])
def test_project_identity_upgrade_reuses_matching_legacy_task(
    tmp_path: Path,
    source: str,
) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    payload = {
        "external_id": f"upgrade-{source}",
        "task_id": f"upgrade-{source}",
        "goal": "Deliver once across the identity upgrade",
    }
    legacy = normalize_event(source=source, payload=payload, repository=tmp_path)
    identified = normalize_event(
        source=source,
        payload=payload,
        repository=tmp_path,
        project_id="project",
        project_key=trust_key(tmp_path),
    )

    first = enqueue_envelope(queue, legacy)
    second = enqueue_envelope(queue, identified)

    assert second.id == first.id
    assert second.task_key == legacy["task_key"]
    assert len(queue.list()) == 1


def test_legacy_alias_does_not_deduplicate_a_different_repository(
    tmp_path: Path,
) -> None:
    first_repository = tmp_path / "first"
    second_repository = tmp_path / "second"
    first_repository.mkdir()
    second_repository.mkdir()
    queue = TaskQueue(tmp_path / "queue.db")
    payload = {
        "external_id": "same-event",
        "task_id": "same-event",
        "goal": "Repository identity remains part of idempotency",
    }
    legacy = normalize_event(
        source="api", payload=payload, repository=first_repository
    )
    identified = normalize_event(
        source="api",
        payload=payload,
        repository=second_repository,
        project_id="second",
        project_key=trust_key(second_repository),
    )

    first = enqueue_envelope(queue, legacy)
    second = enqueue_envelope(queue, identified)

    assert second.id != first.id
    assert len(queue.list()) == 2


def test_explicit_task_key_is_namespaced_per_project_repository(
    tmp_path: Path,
) -> None:
    first_repository = tmp_path / "first"
    second_repository = tmp_path / "second"
    first_repository.mkdir()
    second_repository.mkdir()
    queue = TaskQueue(tmp_path / "queue.db")
    payload = {
        "external_id": "same-event",
        "task_id": "same-task",
        "task_key": "client:same",
        "goal": "Do not drop a task from another repository",
    }
    first = normalize_event(
        source="api",
        payload=payload,
        repository=first_repository,
        project_id="shared",
        project_key=trust_key(first_repository),
    )
    second = normalize_event(
        source="api",
        payload=payload,
        repository=second_repository,
        project_id="shared",
        project_key=trust_key(second_repository),
    )

    first_record = enqueue_envelope(queue, first)
    second_record = enqueue_envelope(queue, second)

    assert first["task_key"] != second["task_key"]
    assert first["task_key"].endswith(":explicit:client:same")
    assert second["task_key"].endswith(":explicit:client:same")
    assert first_record.id != second_record.id
    assert len(queue.list()) == 2


def test_explicit_task_key_upgrade_reuses_same_repository_legacy_task(
    tmp_path: Path,
) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    payload = {
        "external_id": "same-event",
        "task_id": "same-task",
        "task_key": "client:same",
        "goal": "Reuse the legacy delivery",
    }
    legacy = normalize_event(source="api", payload=payload, repository=tmp_path)
    identified = normalize_event(
        source="api",
        payload=payload,
        repository=tmp_path,
        project_id="project",
        project_key=trust_key(tmp_path),
    )

    first = enqueue_envelope(queue, legacy)
    second = enqueue_envelope(queue, identified)

    assert identified["task_key"] != legacy["task_key"]
    assert second.id == first.id
    assert len(queue.list()) == 1


def test_project_identity_is_optional_for_legacy_events_and_preserved_when_present(
    tmp_path: Path,
) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    first_repository = tmp_path / "first"
    second_repository = tmp_path / "second"
    first_repository.mkdir()
    second_repository.mkdir()
    first_key = trust_key(first_repository)
    second_key = trust_key(second_repository)
    legacy = normalize_event(
        source="api",
        payload={"external_id": "legacy", "task_id": "legacy"},
        repository=tmp_path,
    )
    identified = normalize_event(
        source="api",
        payload={"external_id": "identified", "task_id": "identified"},
        repository=first_repository,
        project="nextjs_web",
        project_id="shared-name",
        project_key=first_key,
    )
    same_event_other_project = normalize_event(
        source="api",
        payload={"external_id": "identified", "task_id": "identified"},
        repository=second_repository,
        project="nextjs_web",
        project_id="shared-name",
        project_key=second_key,
    )

    record = enqueue_envelope(queue, identified)

    assert "project_id" not in legacy
    assert "project_key" not in legacy
    assert identified["project"] == "nextjs_web"
    assert identified["project_id"] == "shared-name"
    assert identified["project_key"] == first_key
    assert identified["task_key"] == f"api:{first_key}:identified:identified"
    assert identified["task_key"] != same_event_other_project["task_key"]
    assert identified["event_id"] != same_event_other_project["event_id"]
    assert record.payload["project_id"] == "shared-name"
    assert record.payload["project_key"] == first_key


def test_event_rejects_project_key_for_another_repository(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()

    with pytest.raises(EventError, match="does not match the canonical repository"):
        normalize_event(
            source="api",
            payload={"external_id": "wrong-project"},
            repository=tmp_path,
            project_id="project",
            project_key=trust_key(other),
        )


def test_task_and_workflow_contracts_require_project_identity_as_a_pair(
    tmp_path: Path,
) -> None:
    envelope = normalize_event(
        source="api",
        payload={"external_id": "paired-project"},
        repository=tmp_path,
        project_id="project",
        project_key=trust_key(tmp_path),
    )
    envelope.pop("project_key")
    task_schema = load_json(ROOT / "schemas" / "task_envelope.schema.json")
    workflow_schema = load_json(ROOT / "schemas" / "agent_workflow.schema.json")

    assert any(
        "project_id, project_key must be present together" in error
        for error in validate_contract(envelope, task_schema, "task_envelope")
    )
    assert any(
        "project_id, project_key must be present together" in error
        for error in validate_contract(
            {"project_id": "project"}, workflow_schema, "agent_workflow"
        )
    )


@pytest.mark.parametrize(
    ("project_id", "project_key", "error"),
    [
        ("project", "", "include project_id and project_key together"),
        ("Project", "a" * 64, "lowercase slug"),
        ("project", "not-a-key", "lowercase SHA-256"),
    ],
)
def test_event_rejects_invalid_project_identity(
    tmp_path: Path,
    project_id: str,
    project_key: str,
    error: str,
) -> None:
    with pytest.raises(EventError, match=error):
        normalize_event(
            source="api",
            payload={"external_id": "bad-project"},
            repository=tmp_path,
            project_id=project_id,
            project_key=project_key,
        )


def test_attachment_metadata_is_preserved_only_when_present(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    manifest = tmp_path / ".agent-runs" / "run-input" / "inputs" / "manifest.json"
    digest = "a" * 64
    envelope = normalize_event(
        source="api",
        payload={
            "external_id": "attachment-event",
            "task_id": "attachment-event",
            "run_id": "run-input",
            "input_manifest": str(manifest),
            "input_manifest_sha256": digest,
            "attachment_count": 2,
            "attachment_runtime_consent": True,
        },
        repository=tmp_path,
    )

    record = enqueue_envelope(queue, envelope)

    assert record.payload["input_manifest"] == str(manifest)
    assert record.payload["input_manifest_sha256"] == digest
    assert record.payload["attachment_count"] == 2
    assert record.payload["attachment_runtime_consent"] is True


def test_event_rejects_non_numeric_queue_controls(tmp_path: Path) -> None:
    with pytest.raises(EventError, match="must be integers"):
        normalize_event(
            source="api",
            payload={"external_id": "bad", "priority": "urgent"},
            repository=tmp_path,
        )


def test_current_branch_workspace_mode_survives_normalization_and_queueing(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    envelope = normalize_event(
        source="cli",
        payload={
            "external_id": "current-1",
            "task_id": "current-1",
            "goal": "Use the checkout",
            "branch": "feature/current",
            "workspace_mode": "current_branch",
        },
        repository=tmp_path,
    )

    record = enqueue_envelope(queue, envelope)

    assert envelope["workspace_mode"] == "checkout"
    assert record.payload["workspace_mode"] == "checkout"


def test_execution_mode_survives_normalization_and_queueing(tmp_path: Path) -> None:
    queue = TaskQueue(tmp_path / "queue.db")
    envelope = normalize_event(
        source="cli",
        payload={"external_id": "goal-1", "task_id": "goal-1", "goal": "Long objective", "mode": "goal"},
        repository=tmp_path,
    )

    record = enqueue_envelope(queue, envelope)

    assert envelope["mode"] == "goal"
    assert record.payload["mode"] == "goal"


def test_event_rejects_unknown_execution_mode(tmp_path: Path) -> None:
    with pytest.raises(EventError, match="mode must be auto, adaptive, fast, full, or goal"):
        normalize_event(
            source="api",
            payload={"external_id": "bad-mode", "mode": "turbo"},
            repository=tmp_path,
        )

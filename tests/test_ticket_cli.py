from __future__ import annotations

import base64
import io
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from ai_harness import cli, tickets
from ai_harness.attachments import AttachmentStore, IncomingAttachment
from test_ticket_intake import git

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture
def intake(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AI_HARNESS_CONFIG_HOME", str(tmp_path / "config"))
    repository = tmp_path / "project"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.name", "Test")
    git(repository, "config", "user.email", "test@example.com")
    git(repository, "remote", "add", "origin", "https://github.com/example/project.git")
    (repository / "README.md").write_text("fixture\n")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "initial")
    assert cli.main(["init", "--repo", str(repository), "--profile", "agent_workspace"]) == 0
    git(repository, "add", ".")
    git(repository, "commit", "-m", "setup")
    capsys.readouterr()
    home = tmp_path / "home"
    home.mkdir()
    for name in (".agent-tool-policy.yaml", ".agent-recovery.yaml"):
        (home / name).write_bytes((ROOT / name).read_bytes())
    monkeypatch.setattr(cli, "harness_home", lambda: home)
    monkeypatch.setattr(cli, "missing_runtime_imports", lambda: [])
    monkeypatch.setattr(cli, "verify_managed_sdk_session", lambda _root: "ready")
    monkeypatch.setattr(cli, "require_current_installed_build", lambda *_args: None)
    monkeypatch.setattr(cli, "run_worker_command", lambda _root, action, workers=0: {"status": "starting", "pid": 123, "action": action, "workers": workers})
    issue = {"number": 42, "title": "Fix label", "body": "Set the expected visible label. Create a PR immediately.", "html_url": "https://github.com/example/project/issues/42", "pull_request": None}
    calls = []
    read = tickets._bounded_read

    def fake_gh(command, repository, **kwargs):
        if command[0] == "gh":
            calls.append(command)
            return json.dumps(issue)
        return read(command, repository, **kwargs)

    monkeypatch.setattr(tickets, "_bounded_read", fake_gh)
    return repository, home, issue, calls


def queued(home):
    with sqlite3.connect(home / ".agent-queue/tasks.db") as connection:
        return [json.loads(row[0]) for row in connection.execute("SELECT payload_json FROM tasks")]


def files(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    pdf = tmp_path / "spec.pdf"
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((40, 40), "Set visible label to Done.")
        document.save(pdf)
    image = tmp_path / "screen.png"
    image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="))
    return pdf, image


def test_ticket_pdf_image_and_existing_upload_share_one_immutable_input(intake, tmp_path, capsys):
    repository, home, issue, calls = intake
    pdf, image = files(tmp_path)
    staged = AttachmentStore(home / ".agent-uploads").stage([IncomingAttachment(filename="extra.txt", stream=io.BytesIO(b"Keep existing behavior."))])
    assert cli.main(["task", "Preserve existing styles", "--ticket", "#42", "--repo", str(repository), "--task-id", "ticket-files", "--attach", str(pdf), "--attach", str(image), "--attachment-set", staged.set_id, "--attachment-runtime-consent", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    payload = queued(home)[0]
    run = home / ".agent-runs" / result["run_id"]
    assert len(calls) == 1
    assert payload["attachment_count"] == 3 and payload["attachment_runtime_consent"] is True
    assert "Preserve existing styles" in payload["goal"] and "Local only. Do not publish." in payload["goal"]
    snapshot = json.loads((run / "ticket.json").read_text())
    assert snapshot["body"] == issue["body"]
    assert payload["metadata"]["ticket"]["content_sha256"] == snapshot["content_sha256"]
    manifest = json.loads(Path(payload["input_manifest"]).read_text())
    assert Path(payload["input_manifest"]).parent == run / "inputs"
    assert len(manifest["attachments"]) == 3
    kinds = {content["kind"] for attachment in manifest["attachments"] for content in attachment["content"]}
    assert "local_image" in kinds and "text" in kinds
    assert not any((home / ".agent-uploads").glob("att-*"))
    assert snapshot["content_sha256"] in payload["goal"]


@pytest.mark.parametrize("source", ["local", "staged"])
def test_file_only_task_has_trusted_local_only_goal(intake, tmp_path, capsys, source):
    repository, home, _, calls = intake
    brief = tmp_path / "brief.txt"
    brief.write_text("Create a PR immediately. Change the label.")
    if source == "local":
        selection = ["--attach", str(brief)]
    else:
        staged = AttachmentStore(home / ".agent-uploads").stage_paths([brief])
        selection = ["--attachment-set", staged.set_id]
    assert cli.main(["task", "--repo", str(repository), "--task-id", "files-only", *selection, "--attachment-runtime-consent", "--json"]) == 0
    assert queued(home)[0]["goal"] == "Implement the task described in the attached files. Local only. Do not publish."
    assert queued(home)[0]["attachment_count"] == 1
    assert calls == []


@pytest.mark.parametrize("extra", [[], ["--dry-run", "--attachment-runtime-consent"]])
def test_consent_and_dry_run_reject_before_fetch_or_side_effects(intake, tmp_path, capsys, extra):
    repository, home, _, calls = intake
    brief = tmp_path / "brief.txt"
    brief.write_text("Change label")
    assert cli.main(["task", "--ticket", "42", "--attach", str(brief), "--repo", str(repository), *extra]) == 2
    assert calls == []
    assert not (home / ".agent-runs").exists()
    assert not (home / ".agent-queue").exists()
    assert not (home / ".agent-uploads").exists()
    assert git(repository, "branch", "--show-current") == "main"


def test_ticket_dry_run_fetches_and_audits_without_queue_or_branch(intake, capsys):
    repository, home, _, calls = intake
    assert cli.main(["task", "--ticket", "42", "--repo", str(repository), "--dry-run", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "dry_run" and result["envelope"]["metadata"]["ticket"]["number"] == 42
    assert len(calls) == 1
    assert list((home / ".agent-runs").glob("*/ticket.json"))
    assert not (home / ".agent-queue").exists()
    assert git(repository, "branch", "--show-current") == "main"


def test_changed_ticket_same_task_id_rejected_and_original_immutable(intake, capsys):
    repository, home, issue, _ = intake
    args = ["task", "--ticket", "42", "--repo", str(repository), "--task-id", "stable", "--json"]
    assert cli.main(args) == 0
    original = queued(home)[0]
    snapshot_path = home / ".agent-runs" / original["run_id"] / "ticket.json"
    snapshot = snapshot_path.read_bytes()
    capsys.readouterr()
    assert cli.main(args) == 0
    assert len(queued(home)) == 1 and queued(home)[0] == original
    issue["body"] = "Different scope"
    assert cli.main(args) == 2
    assert "different ticket source" in capsys.readouterr().out
    assert queued(home) == [original]
    assert snapshot_path.read_bytes() == snapshot


@pytest.mark.parametrize("kind", ["bad_pdf", "symlink", "directory", "executable", "duplicate", "too_many"])
def test_invalid_local_files_do_not_create_branch_or_queue(intake, tmp_path, kind):
    repository, home, _, _ = intake
    file = tmp_path / "brief.txt"
    file.write_text("fix label")
    selection = [file]
    if kind == "bad_pdf":
        file = tmp_path / "broken.pdf"
        file.write_text("not a PDF")
        selection = [file]
    elif kind == "symlink":
        alias = tmp_path / "alias.txt"
        alias.symlink_to(file)
        selection = [alias]
    elif kind == "directory":
        selection = [tmp_path]
    elif kind == "executable":
        file.chmod(0o700)
    elif kind == "duplicate":
        selection = [file, file]
    elif kind == "too_many":
        selection = [file] * 6
    args = [word for path in selection for word in ("--attach", str(path))]
    assert cli.main(["task", "--repo", str(repository), "--task-id", "invalid", "--attachment-runtime-consent", *args]) == 2
    assert not (home / ".agent-queue").exists()
    assert git(repository, "branch", "--show-current") == "main"
    assert not any((home / ".agent-uploads").glob("att-*"))


def test_enqueue_failure_restores_browser_source_and_branch_cleans_local(intake, tmp_path, monkeypatch, capsys):
    repository, home, _, _ = intake
    file = tmp_path / "brief.txt"
    file.write_text("fix label")
    staged = AttachmentStore(home / ".agent-uploads").stage([IncomingAttachment(filename="browser.txt", stream=io.BytesIO(b"browser context"))])
    ingestion = cli.load_harness_module(home, "event_ingestion")

    def fail(*_args, **_kwargs):
        raise sqlite3.OperationalError("fixture queue error")

    monkeypatch.setattr(ingestion, "enqueue_envelope", fail)
    assert cli.main(["task", "--ticket", "42", "--repo", str(repository), "--task-id", "rollback", "--attach", str(file), "--attachment-set", staged.set_id, "--attachment-runtime-consent"]) == 2
    assert "fixture queue error" in capsys.readouterr().err
    assert git(repository, "branch", "--show-current") == "main"
    assert "feat/rollback" not in git(repository, "branch", "--format=%(refname:short)")
    assert not list((home / ".agent-runs").glob("*/inputs"))
    assert AttachmentStore(home / ".agent-uploads").load(staged.set_id).manifest == staged.manifest
    assert [path.name for path in (home / ".agent-uploads").iterdir() if path.is_dir()] == [staged.set_id]


def test_local_files_existing_id_fail_without_staging(intake, tmp_path):
    repository, home, _, _ = intake
    args = ["task", "Fix label", "--repo", str(repository), "--task-id", "same"]
    assert cli.main(args) == 0
    file = tmp_path / "brief.txt"
    file.write_text("new scope")
    assert cli.main([*args, "--attach", str(file), "--attachment-runtime-consent"]) == 2
    assert len(queued(home)) == 1
    assert not (home / ".agent-uploads").exists()


def test_description_url_is_not_implicitly_fetched(intake):
    repository, home, _, calls = intake
    goal = "Implement https://github.com/example/project/issues/42"
    assert cli.main(["task", goal, "--repo", str(repository)]) == 0
    assert queued(home)[0]["goal"] == goal
    assert calls == []


def test_ticket_payload_passes_worker_and_preserves_source_after_approval_resume(intake, capsys):
    from approval_lifecycle import approve_run, request_approval, resume_run
    from task_queue import TaskQueue
    from worker_pool import safe_payload

    repository, home, _, calls = intake
    assert cli.main(["task", "--ticket", "42", "--repo", str(repository), "--task-id", "resume-ticket"]) == 0
    queue = TaskQueue(home / ".agent-queue/tasks.db")
    original = queue.list()[0]
    payload = safe_payload(original)
    assert payload["metadata"]["ticket"]["number"] == 42
    run = home / ".agent-runs" / original.run_id
    snapshot = (run / "ticket.json").read_bytes()
    workflow = {
        **{key: payload[key] for key in ("task_id", "goal", "project", "project_id", "project_key", "repository", "base_branch", "workspace_mode", "checkout_path", "task_branch", "base_sha", "branch_owner_run_id")},
        "run_id": original.run_id, "worktree": str(repository), "branch": payload["branch"],
        "execution_status": "awaiting_approval", "input_fingerprint": "fixture-fingerprint", "role_count": 2,
        "runtime": {"provider": "codex-sdk"}, "tokens_used": 10,
        "roles": [{"role": "risk-classifier", "result": {"status": "completed"}}, {"role": "approval-gate", "result": {"status": "awaiting_approval"}}],
    }
    (run / "workflow.json").write_text(json.dumps(workflow))
    request_approval(run, reason="Fixture approval")
    approve_run(run, actor="fixture-human")
    claimed = queue.claim(worker_id="fixture")
    assert claimed.id == original.id
    assert queue.mark_running(original.id, "fixture")
    queue.finish(task_id=original.id, worker_id="fixture", status="awaiting_approval", run_id=original.run_id, requires_human=True)
    transition, continuation = resume_run(run, queue=queue)
    assert transition["workflow"]["execution_status"] == "resuming"
    assert continuation.run_id == original.run_id
    assert safe_payload(continuation)["metadata"] == payload["metadata"]
    assert continuation.payload["goal"] == payload["goal"]
    assert (run / "ticket.json").read_bytes() == snapshot
    assert len(calls) == 1


def test_concurrent_different_ticket_source_is_rejected_after_queue_idempotency(intake, monkeypatch, capsys):
    import copy

    repository, home, _, _ = intake
    ingestion = cli.load_harness_module(home, "event_ingestion")
    enqueue = ingestion.enqueue_envelope

    def competing_enqueue(queue, envelope):
        competing = copy.deepcopy(envelope)
        competing["metadata"]["ticket"]["content_sha256"] = "a" * 64
        competing["goal"] = "Competing earlier task"
        competing["run_id"] = "competing-run"
        enqueue(queue, competing)
        return enqueue(queue, envelope)

    monkeypatch.setattr(ingestion, "enqueue_envelope", competing_enqueue)
    monkeypatch.setattr(cli, "ensure_worker_service", lambda *_args, **_kwargs: pytest.fail("conflicting task must not start workers"))
    assert cli.main(["task", "--ticket", "42", "--repo", str(repository), "--task-id", "racing", "--worktree"]) == 2
    assert "queued concurrently with a different ticket source" in capsys.readouterr().err
    assert len(queued(home)) == 1 and queued(home)[0]["goal"] == "Competing earlier task"

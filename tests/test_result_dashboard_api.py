from __future__ import annotations

import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from test_result_acceptance import completed_run  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import control_plane_api as api_module  # noqa: E402
from task_queue import TaskQueue  # noqa: E402

TEST_ACCESS = "dashboard-test-token"


@pytest.fixture
def dashboard_server(completed_run: Path):
    queue = TaskQueue(completed_run.parent.parent / "queue.db")
    handler = api_module.handler_factory(
        queue=queue, runs_dir=completed_run.parent, auth_token=TEST_ACCESS,
        webhook_secret="", default_repository=completed_run.parent.parent / "repository",
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", handler, queue
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def request(base: str, path: str, *, body=None, token=TEST_ACCESS, origin=""):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    if origin:
        headers["Origin"] = origin
    value = Request(base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
    with urlopen(value, timeout=5) as response:
        return json.loads(response.read())


def test_feedback_http_roundtrip_keeps_workflow_and_queue_unchanged(dashboard_server, completed_run: Path):
    base, _, queue = dashboard_server
    workflow = (completed_run / "workflow.json").read_bytes()
    before = request(base, "/runs/result-1/result-acceptance")
    saved = request(base, "/runs/result-1/result-acceptance", body={
        "status": "needs_changes", "reason": "Handle quoted empty cells too",
        "actor": "not-trusted-client-actor", "expected_fingerprint": before["result_fingerprint"],
    })
    assert saved["current"]["status"] == "needs_changes"
    assert saved["current"]["actor"] == "dashboard-user"
    assert request(base, "/runs/result-1/result-acceptance")["current"] == saved["current"]
    assert (completed_run / "workflow.json").read_bytes() == workflow
    assert queue.list() == []


@pytest.mark.parametrize("body", [
    {"status": "approved"}, {"status": "needs_changes", "reason": ""},
    {"status": [], "reason": "x"}, {"status": "accepted", "reason": "x" * 2001},
])
def test_feedback_invalid_inputs_never_write_history(dashboard_server, completed_run: Path, body):
    base, _, _ = dashboard_server
    body["expected_fingerprint"] = request(base, "/runs/result-1/result-acceptance")["result_fingerprint"]
    with pytest.raises(HTTPError) as failure:
        request(base, "/runs/result-1/result-acceptance", body=body)
    assert failure.value.code == 400
    assert not (completed_run / "result-acceptance.jsonl").exists()


def test_stale_http_feedback_requires_a_new_review(dashboard_server):
    base, _, _ = dashboard_server
    with pytest.raises(HTTPError) as failure:
        request(base, "/runs/result-1/result-acceptance", body={"status": "accepted", "expected_fingerprint": "old"})
    assert failure.value.code == 409


def test_feedback_private_boundary_and_read_only_server(dashboard_server):
    base, handler, _ = dashboard_server
    for token, origin, code in [("wrong", "", 401), (TEST_ACCESS, "https://foreign.example", 403)]:
        with pytest.raises(HTTPError) as failure:
            request(base, "/runs/result-1/result-acceptance", token=token, origin=origin)
        assert failure.value.code == code
    handler.cli_mutations_enabled = False
    fingerprint = request(base, "/runs/result-1/result-acceptance")["result_fingerprint"]
    with pytest.raises(HTTPError) as failure:
        request(base, "/runs/result-1/result-acceptance", body={"status": "accepted", "expected_fingerprint": fingerprint})
    assert failure.value.code == 409


def test_metrics_never_leak_private_feedback_details(dashboard_server):
    base, handler, _ = dashboard_server
    value = request(base, "/runs/result-1/result-acceptance")
    request(base, "/runs/result-1/result-acceptance", body={
        "status": "rejected", "reason": "Private reviewer observation",
        "expected_fingerprint": value["result_fingerprint"],
    })
    authenticated = request(base, "/metrics")
    assert authenticated["runs"]["items"][0]["result_acceptance"]["current"] == {"status": "rejected"}
    assert "Private reviewer observation" not in json.dumps(authenticated)
    for path in ("/metrics", "/runs"):
        foreign = request(base, path, origin="https://foreign.example")
        assert "result_acceptance" not in json.dumps(foreign)
        assert "result_observation" not in json.dumps(foreign)
    handler.auth_token = ""
    for path in ("/metrics", "/runs"):
        public = request(base, path, token="")
        serialized = json.dumps(public)
        assert "Private reviewer observation" not in serialized
        assert "result_acceptance" not in serialized
        assert "result_observation" not in serialized
        assert "result_comparison" not in serialized


def test_archived_feedback_is_reviewable_without_leaking_its_snapshot(dashboard_server, completed_run: Path):
    base, handler, _ = dashboard_server
    value = request(base, "/runs/result-1/result-acceptance")
    request(base, "/runs/result-1/result-acceptance", body={
        "status": "needs_changes", "reason": "Private historical observation",
        "expected_fingerprint": value["result_fingerprint"],
    })
    handler.default_repository.rename(handler.default_repository.with_name("archived-repository"))
    archived = request(base, "/runs/result-1/result-acceptance")
    assert archived["eligible"] is False
    assert archived["historical"]["assessment"]["reason"] == "Private historical observation"
    assert archived["historical"]["snapshot"]["evidence"]["summary"] == "Fixed empty CSV cells."
    metrics = request(base, "/metrics")
    badge = metrics["runs"]["items"][0]["result_acceptance"]["historical"]
    assert badge == {"status": "needs_changes", "availability": "unavailable"}
    assert "Private historical observation" not in json.dumps(metrics)
    assert "Fixed empty CSV cells." not in json.dumps(metrics)
    handler.auth_token = ""
    assert "result_acceptance" not in json.dumps(request(base, "/runs", token=""))


def test_model_catalog_requires_trust_and_preserves_runtime_result(dashboard_server, monkeypatch):
    base, handler, _ = dashboard_server
    path = "/models?" + urlencode({"repository": str(handler.default_repository)})
    monkeypatch.setattr(handler, "repository_settings", lambda self, repo: ({}, "codex-sdk", (), False))
    with pytest.raises(HTTPError) as failure:
        request(base, path)
    assert failure.value.code == 400
    monkeypatch.setattr(handler, "repository_settings", lambda self, repo: ({}, "codex-sdk", (), True))
    catalog = {"status": "available", "provider": "codex-sdk", "models": [{"id": "returned-model", "display_name": "Returned model"}]}
    calls = []
    monkeypatch.setattr(api_module, "discover_models", lambda **kwargs: calls.append(kwargs) or catalog)
    assert request(base, path) == catalog
    assert calls[0]["provider"] == "codex-sdk"
    with pytest.raises(HTTPError) as failure:
        request(base, path, origin="https://foreign.example")
    assert failure.value.code == 403


def test_task_model_is_passed_to_governed_cli(dashboard_server, monkeypatch):
    base, handler, _ = dashboard_server
    captured = []
    monkeypatch.setattr(handler, "agent_command", lambda self, repository, arguments, **kwargs: captured.append(arguments) or {"task_id": "model-task"})
    value = request(base, "/ui/tasks", body={
        "repository": str(handler.default_repository), "goal": "Fix empty CSV cells",
        "model_override": "returned-model", "execution_mode": "fast",
    })
    assert value["task_id"] == "model-task"
    index = captured[0].index("--model")
    assert captured[0][index + 1] == "returned-model"
    with pytest.raises(HTTPError) as failure:
        request(base, "/ui/tasks", body={"repository": str(handler.default_repository), "goal": "Task", "model_override": ["bad"]})
    assert failure.value.code == 400
    assert len(captured) == 1

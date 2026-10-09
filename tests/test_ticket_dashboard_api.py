from __future__ import annotations

import base64
import json
import shutil
import subprocess
import sys
import threading
from http import HTTPStatus
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest

from ai_harness import cli
from ai_harness.observability.dashboard import DASHBOARD_HTML
from ai_harness.project import load_project_config, register_local_project
from test_control_plane_attachment_config import write_project

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import control_plane_api as api_module  # noqa: E402
from task_queue import TaskQueue  # noqa: E402

TEST_ACCESS = "ticket-dashboard-test"


@pytest.fixture
def ticket_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_HARNESS_CONFIG_HOME", str(tmp_path / "trust"))
    repository = tmp_path / "project"
    repository.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repository)], check=True, capture_output=True)
    write_project(repository, provider="codex-sdk", max_file_bytes=1_000_000, max_task_bytes=2_000_000)
    register_local_project(load_project_config(repository))
    home = tmp_path / "harness"
    queue = TaskQueue(home / ".agent-queue" / "tasks.db")
    handler = api_module.handler_factory(
        queue=queue, runs_dir=home / ".agent-runs", auth_token=TEST_ACCESS,
        webhook_secret="", default_repository=repository,
        attachment_store_root=home / ".agent-uploads",
    )
    calls = []

    def command(_handler, selected_repository, arguments, **kwargs):
        calls.append((selected_repository, arguments, kwargs))
        return {"status": "queued", "task_id": "ticket-test"}

    monkeypatch.setattr(handler, "agent_command", command)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", handler, queue, calls
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def post(base: str, body: dict, *, token: str = TEST_ACCESS, origin: str = "", content_type: str = "application/json") -> dict:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": content_type}
    if origin:
        headers["Origin"] = origin
    request = Request(base + "/ui/tasks", data=json.dumps(body).encode(), headers=headers)
    with urlopen(request, timeout=10) as response:
        assert response.status == 202
        return json.loads(response.read())


@pytest.mark.parametrize("goal", ["", "Keep keyboard navigation and add a focused test"])
@pytest.mark.parametrize("ticket_reference", [
    "https://github.com/example/project/issues/42", "#42", "TEAM-123",
    "https://example.atlassian.net/browse/TEAM-123",
])
def test_ticket_intake_delegates_separate_reference_and_user_instructions(ticket_server, goal: str, ticket_reference: str):
    base, handler, queue, calls = ticket_server
    result = post(base, {
        "repository": str(handler.default_repository), "goal": goal,
        "ticket_reference": f" {ticket_reference} ",
        "task_id": "ticket-custom-id", "execution_mode": "fast", "model_override": "visible-model",
        "workspace_mode": "worktree",
    }, origin=base)
    assert result["task_id"] == "ticket-test"
    assert calls == [(handler.default_repository, [
        "task", *([goal] if goal else []), "--mode", "fast", "--ticket",
        ticket_reference, "--model", "visible-model",
        "--task-id", "ticket-custom-id", "--worktree",
    ], {"timeout": 120})]
    assert queue.list() == []  # Only the governed CLI may enqueue.


@pytest.mark.parametrize("body", [
    {}, {"goal": "  ", "ticket_reference": "  "},
    {"ticket_reference": 42}, {"ticket_reference": ["#42"]},
    {"ticket_reference": None}, {"ticket_reference": "x" * 2049},
])
def test_invalid_ticket_request_never_calls_cli(ticket_server, body: dict):
    base, _, queue, calls = ticket_server
    with pytest.raises(HTTPError) as error:
        post(base, body)
    assert error.value.code == 400
    assert calls == [] and queue.list() == []


@pytest.mark.parametrize("kind,expected", [
    ("wrong_token", 401), ("no_server_token", 403), ("cross_origin", 403),
    ("read_only", 409), ("not_json", 400), ("wrong_project", 404),
])
def test_ticket_intake_keeps_local_request_boundaries(ticket_server, kind: str, expected: int):
    base, handler, queue, calls = ticket_server
    body = {"ticket_reference": "#42"}
    options = {}
    if kind == "wrong_token":
        options["token"] = "wrong"
    elif kind == "no_server_token":
        handler.auth_token = ""
    elif kind == "cross_origin":
        options["origin"] = "https://foreign.example"
    elif kind == "read_only":
        handler.cli_mutations_enabled = False
    elif kind == "not_json":
        options["content_type"] = "text/plain"
    elif kind == "wrong_project":
        body["project_key"] = "f" * 64
    with pytest.raises(HTTPError) as error:
        post(base, body, **options)
    assert error.value.code == expected
    assert calls == [] and queue.list() == []


@pytest.mark.parametrize("ticket_reference", ["#42", "TEAM-123"])
def test_untrusted_ticket_project_is_rejected_by_authoritative_cli(ticket_server, monkeypatch: pytest.MonkeyPatch, ticket_reference: str):
    base, handler, queue, _ = ticket_server
    config = handler.default_repository / ".agent/project.yaml"
    config.write_text(config.read_text().replace("feat/", "changed/"))

    def command(_handler, repository, arguments, **kwargs):
        args = cli.build_parser().parse_args([*arguments, "--repo", str(repository)])
        try:
            cli.handle_task(args)
        except cli.CLIError as exc:
            raise api_module.APIError(HTTPStatus.BAD_REQUEST, str(exc)) from exc
        pytest.fail("untrusted intake must fail before ticket fetch or queue mutation")

    monkeypatch.setattr(handler, "agent_command", command)
    with pytest.raises(HTTPError) as error:
        post(base, {"ticket_reference": ticket_reference})
    assert error.value.code == 400
    assert "not locally trusted" in error.value.read().decode()
    assert queue.list() == []


@pytest.mark.parametrize("stdout,stderr,expected", [
    ('{"status":"error","error":"Unsupported ticket reference; use a github.com issue URL, owner/repo#number, or #number"}', "", "Unsupported ticket reference; use a github.com issue URL, owner/repo#number, or #number"),
    ('{"status":"error","error":"Ticket lookup timed out; retry the explicit request"}', "SDK startup warning", "Ticket lookup timed out; retry the explicit request"),
    ("", '{"status":"error","error":"Ticket access failed","extra":"not a displayed field"}', "Ticket access failed"),
    ("not JSON", "plain failure", "plain failure"),
    ('{"status":"error","error":{}}', "", '{"status":"error","error":{}}'),
    (json.dumps({"status": "error", "error": "x" * 3000}), "", "x" * 2000),
    ('{"status":"error","error":"Could not read Jira; check existing acli Jira authentication and access"}', "", "Could not read Jira; check existing acli Jira authentication and access"),
])
def test_agent_command_error_is_unwrapped_before_http_response(ticket_server, monkeypatch: pytest.MonkeyPatch, stdout: str, stderr: str, expected: str):
    base, handler, queue, calls = ticket_server
    monkeypatch.setattr(handler, "agent_command", api_module.ControlPlaneHandler.agent_command)

    def failed_command(command, **kwargs):
        assert "--ticket" in command and "--json" in command
        assert kwargs["env"]["AI_HARNESS_HOME"] == str(handler.attachment_store_root.parent)
        return subprocess.CompletedProcess(command, 2, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(api_module.subprocess, "run", failed_command)
    with pytest.raises(HTTPError) as error:
        post(base, {"ticket_reference": "invalid-reference"})
    assert error.value.code == 400
    assert json.loads(error.value.read()) == {"status": "error", "error": expected}
    assert calls == [] and queue.list() == []


def _text_pdf() -> bytes:
    text = b"BT /F1 12 Tf 50 700 Td (Use the screenshot and preserve keyboard navigation.) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(text)).encode() + b" >>\nstream\n" + text + b"\nendstream",
    ]
    result = b"%PDF-1.4\n"
    offsets = []
    for number, content in enumerate(objects, 1):
        offsets.append(len(result))
        result += f"{number} 0 obj\n".encode() + content + b"\nendobj\n"
    xref = len(result)
    result += b"xref\n0 6\n0000000000 65535 f \n"
    result += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    return result + f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


@pytest.mark.parametrize("goal,ticket", [
    ("Use the selected requirements and screen", "#42"), ("", "#42"), ("", ""),
    ("Use the selected requirements and screen", "TEAM-123"),
])
def test_ticket_with_text_pdf_and_image_uses_existing_consent_and_cli_contract(ticket_server, goal: str, ticket: str):
    base, handler, queue, calls = ticket_server
    image = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
    set_ids = []
    for name, content, mime in [("requirements.pdf", _text_pdf(), "application/pdf"), ("screen.png", image, "image/png")]:
        query = urlencode({"name": name, "repository": str(handler.default_repository)})
        request = Request(base + "/ui/attachments?" + query, data=content, headers={
            "Authorization": f"Bearer {TEST_ACCESS}", "Content-Type": mime,
        })
        with urlopen(request, timeout=15) as response:
            uploaded = json.loads(response.read())
        set_ids.append(uploaded["set_id"])
    body = {"goal": goal, "ticket_reference": ticket, "attachment_set_ids": set_ids}
    with pytest.raises(HTTPError) as error:
        post(base, body)
    assert error.value.code == 400
    assert "confirm" in error.value.read().decode()
    assert calls == []
    post(base, {**body, "attachment_runtime_consent": True})
    assert calls[0][1] == [
        "task", *([goal] if goal else []), "--mode", "auto", *(["--ticket", ticket] if ticket else []),
        "--attachment-set", set_ids[0], "--attachment-set", set_ids[1],
        "--attachment-runtime-consent",
    ]
    assert calls[0][2]["timeout"] == api_module.ATTACHMENT_TASK_TIMEOUT_SECONDS
    assert queue.list() == []
    assert all((handler.attachment_store_root / set_id).is_dir() for set_id in set_ids)


def test_composer_ticket_draft_survives_failure_and_clears_only_after_success():
    fields = {}

    class Fields(HTMLParser):
        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag in {"input", "textarea"} and values.get("id"):
                fields[values["id"]] = values

    Fields().feed(DASHBOARD_HTML)
    assert "required" not in fields["goal"]  # Native validation must allow ticket/file-only submit.
    assert fields["ticketReference"]["aria-describedby"] == "ticketHelp"
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is needed for the isolated dashboard interaction regression")
    version = subprocess.run([node, "--version"], check=True, text=True, capture_output=True)
    if int(version.stdout.strip().removeprefix("v").split(".")[0]) < 18:
        pytest.skip("The dashboard interaction regression requires Node 18 or newer")
    script = DASHBOARD_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
    syntax = subprocess.run([node, "--check"], input=script, text=True, capture_output=True, timeout=10)
    assert syntax.returncode == 0, syntax.stderr
    start = DASHBOARD_HTML.index("function taskErrorMessage(error){")
    end = DASHBOARD_HTML.index("let batchRowCounter=", start)
    code = r"""import assert from 'node:assert/strict';
const elements = new Map();
const $ = id => {if(!elements.has(id))elements.set(id,{value:'',checked:false,inert:false,disabled:false,setAttribute(){},removeAttribute(){},focus(){this.focused=true}});return elements.get(id)};
const file={name:'screen.png'};
const state={connected:true,submitting:false,contextFiles:[file],pendingTaskId:''};
const taskAttachmentApi={runtimeProvider:'codex-sdk'};
const selectedProject=()=>({project_key:'selected-key',project_id:'selected-project',can_create_tasks:true});
const projectStateHelp=()=>'';
const renderContextFiles=()=>{};
const clearContextFiles=()=>{state.contextFiles=[]};
const messages=[];
const toast=message=>messages.push(message);
const setContextFileStatus=()=>{};
const load=async()=>{};
const setDashboardView=()=>{};
const focusPendingTask=()=>{};
const requestAnimationFrame=()=>{};
let calls=[],uploads=0,fail=true,failureMessage='Unsupported ticket reference; use a github.com issue URL, owner/repo#number, or #number';
const uploadTaskAttachments=async(files,selection)=>{uploads++;assert.equal(selection.projectKey,'selected-key');if(!files.length)return {};assert.deepEqual(files,[file]);return {attachment_set_ids:['staged-image']}};
const api=async(path,options)=>{calls.push({path,body:options.body});if(fail)throw new Error(failureMessage);return {task_id:'queued-ticket'}};
$('ticketReference').value='#42';$('repository').value='/selected/project';$('goal').value='Keep keyboard navigation';
$('taskId').value='stable-retry-id';$('taskModel').value='visible-model';$('executionMode').value='fast';$('workspaceMode').value='worktree';
"""
    code += DASHBOARD_HTML[start:end]
    code += r"""
await startTask({preventDefault(){}});
assert.equal(calls.length,0);assert.equal(uploads,0);assert.equal($('attachmentRuntimeConsent').focused,true);
$('attachmentRuntimeConsent').checked=true;
await startTask({preventDefault(){}});
assert.equal(calls.length,1);assert.equal($('goal').value,'Keep keyboard navigation');assert.equal($('ticketReference').value,'#42');
assert.equal(messages.at(-1),'Не удалось распознать задачу. Укажите GitHub issue, #42, ключ Jira (TEAM-123) или ссылку Jira Cloud.');
assert.equal(taskErrorMessage(new Error('An unrelated task failure')),'An unrelated task failure');
assert.equal(taskErrorMessage(new Error('constructor')),'constructor');
assert.equal($('taskId').value,'stable-retry-id');assert.deepEqual(state.contextFiles,[file]);assert.equal(state.submitting,false);assert.equal($('taskForm').inert,false);
state.submitting=true;await startTask({preventDefault(){}});assert.equal(calls.length,1);state.submitting=false;
fail=false;$('goal').value='';
await startTask({preventDefault(){}});
assert.equal(calls.length,2);assert.equal(calls[1].path,'/ui/tasks');assert.equal(calls[1].body.goal,'');assert.equal(calls[1].body.ticket_reference,'#42');
assert.equal(calls[1].body.task_id,'stable-retry-id');assert.equal(calls[1].body.attachment_runtime_consent,true);
assert.equal(calls[1].body.model_override,'visible-model');assert.deepEqual(calls[1].body.attachment_set_ids,['staged-image']);
assert.equal($('goal').value,'');assert.equal($('ticketReference').value,'');assert.equal($('taskId').value,'');assert.deepEqual(state.contextFiles,[]);
await startTask({preventDefault(){}});assert.equal(calls.length,2);assert.equal($('goal').focused,true);
state.contextFiles=[file];$('attachmentRuntimeConsent').checked=true;
await startTask({preventDefault(){}});assert.equal(calls.length,3);assert.equal(calls[2].body.goal,'');assert.equal(calls[2].body.ticket_reference,undefined);
assert.equal(calls[2].body.attachment_runtime_consent,true);assert.deepEqual(calls[2].body.attachment_set_ids,['staged-image']);
$('ticketReference').value='#43';await startTask({preventDefault(){}});
assert.equal(calls.length,4);assert.equal(calls[3].body.goal,'');assert.equal(calls[3].body.ticket_reference,'#43');assert.equal(calls[3].body.attachment_set_ids,undefined);
$('goal').value='Text-only task';await startTask({preventDefault(){}});
assert.equal(calls.length,5);assert.equal(calls[4].body.goal,'Text-only task');assert.equal(calls[4].body.ticket_reference,undefined);assert.equal(calls[4].body.attachment_runtime_consent,undefined);
state.contextFiles=[file];$('attachmentRuntimeConsent').checked=true;$('ticketReference').value='TEAM-123';$('goal').value='Jira instructions';$('taskId').value='jira-retry';
fail=true;failureMessage='Jira intake requires the official acli client with an existing Jira login; set it up separately and retry';
await startTask({preventDefault(){}});assert.equal(calls.length,6);
assert.equal(messages.at(-1),'Для Jira нужен официальный Atlassian CLI (acli) с выполненным входом. Настройте его и повторите запуск.');
assert.equal($('ticketReference').value,'TEAM-123');assert.equal($('goal').value,'Jira instructions');assert.equal($('taskId').value,'jira-retry');assert.deepEqual(state.contextFiles,[file]);
failureMessage='Jira ticket site does not match the active acli site; switch it separately before retrying';
await startTask({preventDefault(){}});assert.equal(calls.length,7);
assert.equal(messages.at(-1),'Ссылка относится к другому сайту Jira. Выберите нужный сайт в acli и повторите запуск.');
assert.equal(state.submitting,false);assert.equal($('taskForm').inert,false);assert.deepEqual(state.contextFiles,[file]);
failureMessage='current checkout has uncommitted changes; commit or stash them before queueing the task: README.md';
await startTask({preventDefault(){}});assert.equal(calls.length,8);
assert.equal(messages.at(-1),'В проекте есть ваши несохранённые изменения. Сохраните их в Git или выберите «Параллельная задача», чтобы работать в отдельной копии. Черновик сохранён.');
assert.equal($('goal').value,'Jira instructions');assert.equal($('taskId').value,'jira-retry');assert.deepEqual(state.contextFiles,[file]);
assert.equal(state.submitting,false);assert.equal($('taskForm').inert,false);
"""
    completed = subprocess.run([node, "--input-type=module", "-"], input=code, text=True, capture_output=True, timeout=10)
    assert completed.returncode == 0, completed.stderr

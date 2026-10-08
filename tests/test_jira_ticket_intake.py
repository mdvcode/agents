from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

from ai_harness import cli, tickets
from test_ticket_intake import ticket_project
from test_ticket_cli import intake, queued


@pytest.fixture
def jira_reader(monkeypatch):
    state = {
        "status": "✓ Authenticated\n  Site: example.atlassian.net\n  Email: private@example.invalid\n  Authentication Type: oauth\n",
        "issue": {"key": "DEMO-42", "self": "https://example.atlassian.net/rest/api/3/issue/10042", "fields": {"summary": "Fix label", "description": {"version": 1, "type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Keep café label."}]}, {"type": "bulletList", "content": [{"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Verify label."}]}]}]}, {"type": "mediaSingle", "content": [{"type": "media", "attrs": {"id": "media-1", "type": "file", "collection": "private"}}]}]}}},
    }
    calls = []
    original = tickets._bounded_read

    def read(command, repository, **kwargs):
        if command[0] != "acli":
            return original(command, repository, **kwargs)
        calls.append((command, kwargs))
        if command == ["acli", "jira", "auth", "status"]:
            return state["status"]
        assert command == ["acli", "jira", "workitem", "view", "DEMO-42", "--fields", "key,summary,description", "--json"]
        return json.dumps(state["issue"])

    monkeypatch.setattr(tickets, "_bounded_read", read)
    return state, calls


@pytest.mark.parametrize("reference", ["DEMO-42", "https://example.atlassian.net/browse/DEMO-42"])
def test_jira_site_bound_read_extracts_adf_and_never_fetches_media(ticket_project, jira_reader, reference):
    repository, home = ticket_project
    state, calls = jira_reader
    run = home / ".agent-runs/jira"
    snapshot = tickets.resolve_ticket(reference, repository, control_root=home, run_dir=run)
    assert snapshot["provider"] == "jira" and snapshot["key"] == "DEMO-42"
    assert snapshot["repository"] == "DEMO" and snapshot["number"] == 42
    assert snapshot["url"] == "https://example.atlassian.net/browse/DEMO-42"
    assert "Keep café label." in snapshot["body"] and "Verify label." in snapshot["body"]
    assert "content was not fetched" in snapshot["body"]
    assert len(calls) == 2 and all(value[1]["timeout"] <= 30 for value in calls)
    assert calls[0][1]["maximum"] == 8192 and calls[1][1]["maximum"] == tickets.MAX_RESPONSE_BYTES
    assert snapshot["description_sha256"]
    goal = tickets.ticket_goal(snapshot)
    assert "Implement Jira work item DEMO-42" in goal and "Local only. Do not publish." in goal
    for path in run.rglob("*"):
        if path.is_file():
            assert "private@example.invalid" not in path.read_text()
    audit = [json.loads(line) for line in (run / "raw-events/tool-calls.jsonl").read_text().splitlines()]
    assert all(value["tool"] == "jira_ticket_source" and value["credential_type"] == "acli_auth" for value in audit)


@pytest.mark.parametrize("reference", ["demo-42", "DEMO-0", "https://example.invalid/browse/DEMO-42", "https://example.atlassian.net/browse/DEMO-42?x=1", "https://example.atlassian.net.evil.invalid/browse/DEMO-42", "https://user:pass@example.atlassian.net/browse/DEMO-42", "https://example.atlassian.net/browse/DEMO-42#comment", "DEMO-42\n"])
def test_malformed_jira_reference_has_no_cli_calls(ticket_project, jira_reader, reference):
    repository, home = ticket_project
    _, calls = jira_reader
    with pytest.raises(tickets.TicketError):
        tickets.resolve_ticket(reference, repository, control_root=home, run_dir=home / ".agent-runs/jira")
    assert calls == [] and not (home / ".agent-runs").exists()


def test_jira_url_wrong_active_site_fails_before_issue_read(ticket_project, jira_reader):
    repository, home = ticket_project
    _, calls = jira_reader
    with pytest.raises(tickets.TicketError, match="does not match"):
        tickets.resolve_ticket("https://other.atlassian.net/browse/DEMO-42", repository, control_root=home, run_dir=home / ".agent-runs/jira")
    assert [value[0] for value in calls] == [["acli", "jira", "auth", "status"]]
    assert not list((home / ".agent-runs").glob("*/ticket.json"))


@pytest.mark.parametrize("status", ["Not authenticated\nSite: example.atlassian.net\n", "✓ Authenticated\nSite: example.atlassian.net\nSite: other.atlassian.net\n", "✓ Authenticated\nSee https://example.atlassian.net\n", "✓ Authenticated\nSite: https://private.invalid\n", "✓ Authenticated\nSite: https://user:pass@example.atlassian.net\n"])
def test_jira_ambiguous_or_unsupported_auth_status_has_no_issue_read(ticket_project, jira_reader, status):
    repository, home = ticket_project
    state, calls = jira_reader
    state["status"] = status
    with pytest.raises(tickets.TicketError, match="exactly one"):
        tickets.resolve_ticket("DEMO-42", repository, control_root=home, run_dir=home / ".agent-runs/jira")
    assert len(calls) == 1


@pytest.mark.parametrize("override", [{"key": "OTHER-42"}, {"self": "https://other.atlassian.net/rest/api/3/issue/10042"}, {"self": "https://example.atlassian.net/rest/api/3/issue/10042?token=x"}, {"fields": {"summary": "Changed", "description": [], "project": {"key": "OTHER"}}}, {"fields": {"summary": "x" * 513}}, {"fields": {"summary": "valid", "description": "x" * 14001}}, {"fields": {"summary": "valid", "description": {"type": "unknown", "content": []}}}])
def test_jira_response_identity_and_bounds_fail_closed(ticket_project, jira_reader, override):
    repository, home = ticket_project
    state, _ = jira_reader
    state["issue"].update(override)
    run = home / ".agent-runs/jira"
    with pytest.raises(tickets.TicketError):
        tickets.resolve_ticket("DEMO-42", repository, control_root=home, run_dir=run)
    assert not (run / "ticket.json").exists()
    assert "ticket-intake-failed" in (run / "raw-events/tool-calls.jsonl").read_text()


def test_jira_central_policy_denial_occurs_before_auth_status(ticket_project, jira_reader):
    repository, home = ticket_project
    _, calls = jira_reader
    policy = yaml.safe_load((home / ".agent-tool-policy.yaml").read_text())
    policy["tools"].pop("jira_ticket_source")
    (home / ".agent-tool-policy.yaml").write_text(yaml.safe_dump(policy))
    with pytest.raises(tickets.TicketError, match="not allowed"):
        tickets.resolve_ticket("DEMO-42", repository, control_root=home, run_dir=home / ".agent-runs/jira")
    assert calls == []


def test_missing_acli_is_actionable_without_auth_or_queue_changes(ticket_project, monkeypatch):
    repository, home = ticket_project

    monkeypatch.setenv("PATH", str(home / "no-programs"))
    with pytest.raises(tickets.TicketError, match="official acli client with an existing Jira login"):
        tickets.resolve_ticket("DEMO-42", repository, control_root=home, run_dir=home / ".agent-runs/jira")
    assert not (home / ".agent-queue").exists()


@pytest.mark.parametrize("kind", ["depth", "nodes", "text"])
def test_adf_traversal_limits(kind):
    node = {"type": "text", "text": "x"}
    if kind == "depth":
        for _ in range(34):
            node = {"type": "paragraph", "content": [node]}
        content = [node]
    elif kind == "nodes":
        content = [node] * 2001
    else:
        content = [{"type": "text", "text": "x" * 14001}]
    with pytest.raises(tickets.TicketError, match="limit"):
        tickets._jira_description({"type": "doc", "version": 1, "content": content})


def test_jira_real_queue_and_retry_keep_exact_selected_source(intake, jira_reader, capsys):
    repository, home, _, _ = intake
    state, calls = jira_reader
    args = ["task", "--ticket", "DEMO-42", "--repo", str(repository), "--task-id", "jira", "--json"]
    assert cli.main(args) == 0
    original = queued(home)[0]
    assert original["metadata"]["ticket"]["provider"] == "jira"
    assert "Jira work item DEMO-42" in original["goal"]
    assert cli.main(args) == 0
    assert queued(home) == [original]
    state["issue"]["fields"]["description"]["content"][-1]["content"][0]["attrs"]["id"] = "different-media"
    assert cli.main(args) == 2
    assert "different ticket source" in capsys.readouterr().out
    assert queued(home) == [original]
    assert len(calls) == 6


def test_jira_json_array_is_not_the_official_single_item_contract(ticket_project, jira_reader):
    repository, home = ticket_project
    state, _ = jira_reader
    state["issue"] = [state["issue"]]
    with pytest.raises(tickets.TicketError, match="different work item"):
        tickets.resolve_ticket("DEMO-42", repository, control_root=home, run_dir=home / ".agent-runs/jira")


def test_jira_combined_deadline_expires_before_second_call(ticket_project, jira_reader, monkeypatch):
    repository, home = ticket_project
    _, calls = jira_reader
    times = iter([100.0, 131.0])
    monkeypatch.setattr(tickets.time, "monotonic", lambda: next(times))
    with pytest.raises(tickets.TicketError, match="timed out"):
        tickets.resolve_ticket("DEMO-42", repository, control_root=home, run_dir=home / ".agent-runs/jira")
    assert len(calls) == 1 and calls[0][1]["timeout"] == 5


def test_malformed_adf_type_is_a_validation_error():
    with pytest.raises(tickets.TicketError, match="invalid"):
        tickets._jira_description({"type": "doc", "version": 1, "content": [{"type": []}]})


def test_acli_subprocess_uses_official_endpoints_and_private_cwd(ticket_project, tmp_path, monkeypatch):
    repository, home = ticket_project
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    evidence = tmp_path / "calls.jsonl"
    (repository / ".env").write_text("ATLASSIAN_API_URL=https://untrusted.invalid\n")
    script = binary_dir / "acli"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "assert not Path('.env').exists()\n"
        "assert os.environ['ATLASSIAN_API_URL'] == 'https://api.atlassian.com'\n"
        "assert os.environ['ATLASSIAN_AUTH_URL'] == 'https://auth.atlassian.com/authorize?audience='\n"
        "assert os.environ['ATLASSIAN_ACCESS_TOKEN_URL'] == 'https://auth.atlassian.com/oauth/token'\n"
        f"with open({str(evidence)!r}, 'a') as handle: handle.write(json.dumps({{'args':sys.argv[1:], 'cwd':os.getcwd()}})+'\\n')\n"
        "if sys.argv[1:] == ['jira', 'auth', 'status']:\n"
        "    print('✓ Authenticated\\n  Site: example.atlassian.net')\n"
        "else:\n"
        "    assert sys.argv[1:] == ['jira', 'workitem', 'view', 'DEMO-42', '--fields', 'key,summary,description', '--json']\n"
        "    print(json.dumps({'key':'DEMO-42','self':'https://example.atlassian.net/rest/api/3/issue/10042','fields':{'summary':'Fix label','description':'Preserve behavior.'}}))\n"
    )
    script.chmod(0o700)
    monkeypatch.setenv("PATH", str(binary_dir) + os.pathsep + os.environ.get("PATH", ""))
    for key in ("ATLASSIAN_API_URL", "ATLASSIAN_AUTH_URL", "ATLASSIAN_ACCESS_TOKEN_URL"):
        monkeypatch.setenv(key, "https://untrusted.invalid")
    snapshot = tickets.resolve_ticket("DEMO-42", repository, control_root=home, run_dir=home / ".agent-runs/jira")
    assert snapshot["body"] == "Preserve behavior."
    calls = [json.loads(line) for line in evidence.read_text().splitlines()]
    assert len(calls) == 2
    assert all(Path(value["cwd"]) != repository and not Path(value["cwd"]).exists() for value in calls)

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
import yaml

from ai_harness.project import (
    CONFIG_RELATIVE_PATH,
    default_config,
    load_local_trust,
    register_local_project,
    trust_key,
    write_project_config,
)
from ai_harness.project_catalog import (
    ProjectNotFoundError,
    ProjectUnavailableError,
    get_project,
    load_project_catalog,
    resolve_project_key,
)


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from control_plane_api import ControlPlaneHandler, handler_factory
from task_queue import TaskQueue


def register_project(
    repository: Path,
    registry: Path,
    *,
    project_id: str = "shared-project",
) -> str:
    repository.mkdir(parents=True)
    config = default_config(
        repository,
        project_id=project_id,
        profile="agent_workspace",
        base_branch="main",
    )
    write_project_config(config)
    register_local_project(config, path=registry)
    return trust_key(repository)


def request_json(
    url: str,
    token: str = "",
    *,
    method: str = "GET",
    body: dict[str, object] | None = None,
) -> dict[str, object]:
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=5) as response:
        value = json.loads(response.read())
    assert isinstance(value, dict)
    return value


def test_catalog_keeps_duplicate_display_ids_distinct_and_aggregates_by_repository(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "config" / "projects.yaml"
    first = tmp_path / "first"
    second = tmp_path / "second"
    first_key = register_project(first, registry)
    second_key = register_project(second, registry)

    projects = load_project_catalog(
        registry_path=registry,
        tasks=[
            {
                "payload": {"repository": str(first)},
                "status": "running",
                "updated_at": 10.0,
            },
            {
                "payload": {"repository": str(first / ".")},
                "status": "awaiting_approval",
                "updated_at": 12.0,
            },
        ],
        runs=[
            {
                "repository": str(first),
                "status": "completed",
                "updated_at": 15.0,
            }
        ],
    )

    assert {project["project_key"] for project in projects} == {
        first_key,
        second_key,
    }
    assert [project["project_id"] for project in projects].count("shared-project") == 2
    by_key = {project["project_key"]: project for project in projects}
    assert by_key[first_key]["state"] == "ready"
    assert by_key[first_key]["counts"] == {
        "tasks": 2,
        "runs": 1,
        "active_tasks": 1,
        "attention_tasks": 1,
        "codex_conflicts": 2,
    }
    assert by_key[first_key]["task_status_counts"] == {
        "awaiting_approval": 1,
        "running": 1,
    }
    assert by_key[first_key]["run_status_counts"] == {"completed": 1}
    assert by_key[first_key]["last_activity_at"] == 15.0
    assert by_key[second_key]["counts"] == {
        "tasks": 0,
        "runs": 0,
        "active_tasks": 0,
        "attention_tasks": 0,
        "codex_conflicts": 0,
    }


def test_catalog_preserves_all_non_ready_states_without_discovery(tmp_path: Path) -> None:
    registry = tmp_path / "config" / "projects.yaml"
    ready = tmp_path / "ready"
    ready_key = register_project(ready, registry, project_id="ready")
    missing = tmp_path / "missing"
    needs_reinit = tmp_path / "needs-reinit"
    invalid = tmp_path / "invalid"
    needs_reinit.mkdir()
    invalid.mkdir()
    invalid_config = invalid / CONFIG_RELATIVE_PATH
    invalid_config.parent.mkdir(parents=True)
    invalid_config.write_text("version: [not-a-number]\n", encoding="utf-8")

    document = load_local_trust(registry)
    document["projects"].update(
        {
            trust_key(missing): {
                "repository": str(missing.resolve()),
                "project_id": "missing",
                "profile": "agent_workspace",
                "config_fingerprint": "stale",
            },
            trust_key(needs_reinit): {
                "repository": str(needs_reinit.resolve()),
                "project_id": "needs-reinit",
                "profile": "agent_workspace",
                "config_fingerprint": "stale",
            },
            trust_key(invalid): {
                "repository": str(invalid.resolve()),
                "project_id": "invalid",
                "profile": "agent_workspace",
                "config_fingerprint": "stale",
            },
        }
    )
    registry.write_text(
        yaml.safe_dump(document, sort_keys=True), encoding="utf-8"
    )

    states = {
        project["project_key"]: project["state"]
        for project in load_project_catalog(registry_path=registry)
    }

    assert states[ready_key] == "ready"
    assert states[trust_key(missing)] == "missing"
    assert states[trust_key(needs_reinit)] == "needs_reinit"
    assert states[trust_key(invalid)] == "invalid_config"
    assert resolve_project_key(ready_key, registry_path=registry) == ready.resolve()
    with pytest.raises(ProjectUnavailableError) as unavailable:
        resolve_project_key(trust_key(missing), registry_path=registry)
    assert unavailable.value.state == "missing"
    with pytest.raises(ProjectNotFoundError):
        get_project("../not-a-project", registry_path=registry)


def test_catalog_marks_registry_key_path_mismatch_invalid(tmp_path: Path) -> None:
    registry = tmp_path / "config" / "projects.yaml"
    repository = tmp_path / "repository"
    valid_key = register_project(repository, registry)
    document = load_local_trust(registry)
    registered = document["projects"].pop(valid_key)
    wrong_key = "0" * 64 if valid_key != "0" * 64 else "1" * 64
    document["projects"][wrong_key] = registered
    registry.write_text(yaml.safe_dump(document), encoding="utf-8")

    project = get_project(wrong_key, registry_path=registry)

    assert project["state"] == "invalid_config"
    assert project["can_create_tasks"] is False
    with pytest.raises(ProjectUnavailableError):
        resolve_project_key(wrong_key, registry_path=registry)


def test_catalog_requires_reinit_after_valid_config_changes(tmp_path: Path) -> None:
    registry = tmp_path / "config" / "projects.yaml"
    repository = tmp_path / "repository"
    project_key = register_project(repository, registry)
    config_path = repository / CONFIG_RELATIVE_PATH
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    document["project"]["base_branch"] = "trunk"
    config_path.write_text(
        yaml.safe_dump(document, sort_keys=False), encoding="utf-8"
    )

    project = get_project(project_key, registry_path=registry)

    assert project["state"] == "needs_reinit"
    assert project["base_branch"] == "trunk"
    assert project["can_create_tasks"] is False


def test_project_api_requires_a_configured_token_even_for_read_only_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_home = tmp_path / "config"
    monkeypatch.setenv("AI_HARNESS_CONFIG_HOME", str(config_home))
    repository = tmp_path / "repository"
    register_project(repository, config_home / "projects.yaml")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler_factory(
            queue=TaskQueue(tmp_path / "queue.db"),
            runs_dir=tmp_path / "runs",
            auth_token="",
            webhook_secret="",
            default_repository=repository,
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(HTTPError) as forbidden:
            request_json(f"http://127.0.0.1:{server.server_port}/projects")
        assert forbidden.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_project_api_is_authenticated_and_resolves_key_for_config_attachment_and_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_home = tmp_path / "config"
    monkeypatch.setenv("AI_HARNESS_CONFIG_HOME", str(config_home))
    repository = tmp_path / "repository"
    other = tmp_path / "other"
    other.mkdir()
    project_key = register_project(repository, config_home / "projects.yaml")
    queue = TaskQueue(tmp_path / "queue.db")
    queue.enqueue(
        task_key="project-task",
        payload={"task_id": "project-task", "repository": str(repository)},
    )
    calls: list[tuple[Path, list[str]]] = []

    def command(
        _handler: object,
        selected_repository: Path,
        arguments: list[str],
        *,
        timeout: int = 120,
    ) -> dict[str, object]:
        calls.append((selected_repository, arguments))
        return {"status": "queued", "timeout": timeout}

    monkeypatch.setattr(ControlPlaneHandler, "agent_command", command)
    token = "test" + "-projects-token"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler_factory(
            queue=queue,
            runs_dir=tmp_path / "runs",
            attachment_store_root=tmp_path / "uploads",
            auth_token=token,
            webhook_secret="",
            default_repository=repository,
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with pytest.raises(HTTPError) as unauthorized:
            request_json(f"{base}/projects")
        assert unauthorized.value.code == 401

        catalog = request_json(f"{base}/projects", token)
        assert catalog["schema_version"] == 1
        projects = catalog["projects"]
        assert isinstance(projects, list) and len(projects) == 1
        project = projects[0]
        assert project["project_key"] == project_key
        assert project["state"] == "ready"
        assert project["counts"]["tasks"] == 1
        assert not ({"source", "transcript", "raw_events"} & set(project))

        detail = request_json(f"{base}/projects/{project_key}", token)
        assert detail["project"] == project

        config = request_json(
            f"{base}/config?{urlencode({'project_key': project_key})}", token
        )
        assert config["repository"] == str(repository.resolve())
        assert config["project_key"] == project_key
        assert config["project_id"] == "shared-project"

        attachment_query = urlencode(
            {"project_key": project_key, "name": "context.txt"}
        )
        attachment_request = Request(
            f"{base}/ui/attachments?{attachment_query}",
            data=b"project context",
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "text/plain",
            },
        )
        with urlopen(attachment_request, timeout=5) as response:
            attachment = json.loads(response.read())
        assert attachment["status"] == "complete"

        accepted = request_json(
            f"{base}/ui/tasks",
            token,
            method="POST",
            body={"project_key": project_key, "goal": "Use the selected project"},
        )
        assert accepted["status"] == "queued"
        assert calls == [
            (
                repository.resolve(),
                ["task", "Use the selected project", "--mode", "auto"],
            )
        ]

        direct = request_json(
            f"{base}/tasks",
            token,
            method="POST",
            body={
                "source": "api",
                "project_key": project_key,
                "payload": {
                    "external_id": "project-direct",
                    "task_id": "project-direct",
                    "goal": "Queue against the selected project",
                },
            },
        )
        assert direct["envelope"]["repository"] == str(repository.resolve())
        assert direct["envelope"]["project_key"] == project_key
        assert direct["envelope"]["project_id"] == "shared-project"

        with pytest.raises(HTTPError) as spoofed_identity:
            request_json(
                f"{base}/tasks",
                token,
                method="POST",
                body={
                    "source": "api",
                    "repository": str(repository),
                    "payload": {
                        "external_id": "spoofed-project",
                        "task_id": "spoofed-project",
                        "goal": "Do not accept untrusted project metadata",
                        "project_id": "other-project",
                        "project_key": "f" * 64,
                    },
                },
            )
        assert spoofed_identity.value.code == 400

        with pytest.raises(HTTPError) as mismatch:
            request_json(
                f"{base}/config?{urlencode({'project_key': project_key, 'repository': str(other)})}",
                token,
            )
        assert mismatch.value.code == 400

        with pytest.raises(HTTPError) as missing:
            request_json(f"{base}/projects/{'f' * 64}", token)
        assert missing.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_project_ui_registers_a_folder_and_opens_only_a_trusted_project_in_codex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_home = tmp_path / "config"
    monkeypatch.setenv("AI_HARNESS_CONFIG_HOME", str(config_home))
    repository = tmp_path / "repository"
    project_key = register_project(repository, config_home / "projects.yaml")
    new_repository = tmp_path / "new-project"
    new_repository.mkdir()
    agent_calls: list[tuple[Path, list[str]]] = []
    codex_calls: list[Path] = []

    def command(
        _handler: object,
        selected_repository: Path,
        arguments: list[str],
        *,
        timeout: int = 120,
    ) -> dict[str, object]:
        agent_calls.append((selected_repository, arguments))
        return {"status": "initialized", "timeout": timeout}

    monkeypatch.setattr(ControlPlaneHandler, "agent_command", command)
    monkeypatch.setattr(
        "control_plane_api.open_codex_workspace",
        lambda selected_repository: codex_calls.append(selected_repository),
    )
    queue = TaskQueue(tmp_path / "queue.db")
    token = "test" + "-projects-token"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler_factory(
            queue=queue,
            runs_dir=tmp_path / "runs",
            auth_token=token,
            webhook_secret="",
            default_repository=repository,
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        registered = request_json(
            f"{base}/ui/projects",
            token,
            method="POST",
            body={"repository": str(new_repository)},
        )
        assert registered["status"] == "registered"
        assert registered["repository"] == str(new_repository.resolve())
        assert agent_calls == [(new_repository.resolve(), ["init"])]

        opened = request_json(
            f"{base}/ui/projects/{project_key}/open-codex",
            token,
            method="POST",
            body={},
        )
        assert opened == {
            "status": "opened",
            "project_key": project_key,
            "application": "Codex",
        }
        assert codex_calls == [repository.resolve()]

        queue.enqueue(
            task_key="queued-project-task",
            payload={
                "task_id": "queued-project-task",
                "repository": str(repository.resolve()),
            },
        )
        with pytest.raises(HTTPError) as unfinished:
            request_json(
                f"{base}/ui/projects/{project_key}/open-codex",
                token,
                method="POST",
                body={},
            )
        assert unfinished.value.code == 409
        assert codex_calls == [repository.resolve()]

        confirmed = request_json(
            f"{base}/ui/projects/{project_key}/open-codex",
            token,
            method="POST",
            body={"confirm_concurrent_tasks": True},
        )
        assert confirmed["status"] == "opened"
        assert codex_calls == [repository.resolve(), repository.resolve()]

        with pytest.raises(HTTPError) as unregistered:
            request_json(
                f"{base}/ui/projects/{'f' * 64}/open-codex",
                token,
                method="POST",
                body={},
            )
        assert unregistered.value.code == 404
        assert codex_calls == [repository.resolve(), repository.resolve()]

        with pytest.raises(HTTPError) as missing_folder:
            request_json(
                f"{base}/ui/projects",
                token,
                method="POST",
                body={"repository": str(tmp_path / 'does-not-exist')},
            )
        assert missing_folder.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

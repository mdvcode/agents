from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest
import yaml

from ai_harness.attachments import AttachmentStore, IncomingAttachment
from ai_harness.project import (
    CONFIG_RELATIVE_PATH,
    load_project_config,
    register_local_project,
)


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from control_plane_api import (
    ATTACHMENT_TASK_TIMEOUT_SECONDS,
    ControlPlaneHandler,
    cleanup_attachment_uploads,
    handler_factory,
    serve_control_plane,
)
from task_queue import TaskQueue


def write_project(
    repository: Path,
    *,
    provider: str,
    max_file_bytes: int,
    max_task_bytes: int,
) -> None:
    path = repository / CONFIG_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "project": {
                    "id": repository.name,
                    "profile": "agent_workspace",
                    "repository": ".",
                    "base_branch": "main",
                    "branch_prefix": "feat/",
                },
                "runtime": {"provider": provider},
                "attachments": {
                    "max_files": 5,
                    "max_file_bytes": max_file_bytes,
                    "max_task_bytes": max_task_bytes,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def json_request(
    url: str,
    token: str,
    *,
    method: str = "GET",
    body: dict[str, object] | None = None,
) -> dict[str, object]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(
        url,
        data=data,
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


def upload(
    base: str,
    token: str,
    repository: Path,
    filename: str,
    content: bytes,
) -> dict[str, object]:
    query = urlencode({"name": filename, "repository": str(repository)})
    request = Request(
        f"{base}/ui/attachments?{query}",
        data=content,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "text/plain",
        },
    )
    with urlopen(request, timeout=5) as response:
        value = json.loads(response.read())
    assert isinstance(value, dict)
    return value


def test_control_plane_uses_trusted_project_limits_and_runtime_capabilities(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_HARNESS_CONFIG_HOME", str(tmp_path / "trust"))
    sdk_repository = tmp_path / "sdk-project"
    cli_repository = tmp_path / "cli-project"
    sdk_repository.mkdir()
    cli_repository.mkdir()
    write_project(
        sdk_repository,
        provider="codex-sdk",
        max_file_bytes=8,
        max_task_bytes=10,
    )
    register_local_project(load_project_config(sdk_repository))
    write_project(
        cli_repository,
        provider="codex-cli",
        max_file_bytes=8,
        max_task_bytes=10,
    )
    register_local_project(load_project_config(cli_repository))

    calls: list[tuple[Path, list[str]]] = []

    def command(
        _handler: object,
        repository: Path,
        arguments: list[str],
        *,
        timeout: int = 120,
    ) -> dict[str, object]:
        calls.append((repository, arguments))
        return {"status": "queued", "task_id": "configured", "timeout": timeout}

    monkeypatch.setattr(ControlPlaneHandler, "agent_command", command)
    runs = tmp_path / "harness" / ".agent-runs"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler_factory(
            queue=TaskQueue(tmp_path / "queue.db"),
            runs_dir=runs,
            attachment_store_root=runs.parent / ".agent-uploads",
            auth_token="attachment-config-token",
            webhook_secret="",
            default_repository=sdk_repository,
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    token = "attachment-config-token"
    try:
        sdk_config = json_request(
            f"{base}/config?{urlencode({'repository': str(sdk_repository)})}", token
        )
        cli_config = json_request(
            f"{base}/config?{urlencode({'repository': str(cli_repository)})}", token
        )

        assert sdk_config["runtime_provider"] == "codex-sdk"
        assert sdk_config["capabilities"] == ["text", "local_image"]
        assert sdk_config["project_config_trusted"] is True
        assert sdk_config["attachments"] == {
            "enabled": True,
            "runtime_context_enabled": True,
            "runtime_consent_required": True,
            "scanned_pdf_pages_supported": True,
            "endpoint": "/ui/attachments",
            "max_files": 5,
            "max_file_bytes": 8,
            "max_task_bytes": 10,
            "max_runtime_image_bytes": 10 * 1024 * 1024,
            "max_runtime_image_references": 20,
            "max_initial_text_bytes": 120_000,
            "max_initial_text_bytes_per_reference": 24_000,
            "ttl_hours": 24,
            "accepted": ["text", "pdf", "png", "jpeg", "gif"],
        }
        assert cli_config["runtime_provider"] == "codex-cli"
        assert cli_config["capabilities"] == ["text"]
        assert cli_config["attachments"]["accepted"] == ["text", "pdf"]  # type: ignore[index]
        assert cli_config["attachments"]["scanned_pdf_pages_supported"] is False  # type: ignore[index]

        with pytest.raises(HTTPError) as oversized:
            upload(base, token, sdk_repository, "large.txt", b"123456789")
        assert oversized.value.code == 413

        first = upload(base, token, sdk_repository, "first.txt", b"123456")
        second = upload(base, token, sdk_repository, "second.txt", b"abcdef")
        with pytest.raises(HTTPError) as over_task:
            json_request(
                f"{base}/ui/tasks",
                token,
                method="POST",
                body={
                    "repository": str(sdk_repository),
                    "goal": "Use both files",
                    "attachment_set_ids": [first["set_id"], second["set_id"]],
                    "attachment_runtime_consent": True,
                },
            )
        assert over_task.value.code == 413
        assert calls == []

        valid_first = upload(base, token, sdk_repository, "valid-first.txt", b"1234")
        valid_second = upload(base, token, sdk_repository, "valid-second.txt", b"abcd")
        accepted = json_request(
            f"{base}/ui/tasks",
            token,
            method="POST",
            body={
                "repository": str(sdk_repository),
                "goal": "Use valid files",
                "attachment_set_ids": [
                    valid_first["set_id"],
                    valid_second["set_id"],
                ],
                "attachment_runtime_consent": True,
            },
        )
        assert accepted["timeout"] == ATTACHMENT_TASK_TIMEOUT_SECONDS
        assert calls == [
            (
                sdk_repository,
                [
                    "task",
                    "Use valid files",
                    "--mode",
                    "auto",
                    "--attachment-set",
                    valid_first["set_id"],
                    "--attachment-set",
                    valid_second["set_id"],
                    "--attachment-runtime-consent",
                ],
            )
        ]

        image_query = urlencode(
            {"name": "context.png", "repository": str(cli_repository)}
        )
        image_request = Request(
            f"{base}/ui/attachments?{image_query}",
            data=b"x",
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "image/png",
            },
        )
        with pytest.raises(HTTPError) as unsupported_image:
            urlopen(image_request, timeout=5)
        assert unsupported_image.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_untrusted_project_cannot_raise_attachment_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_HARNESS_CONFIG_HOME", str(tmp_path / "empty-trust"))
    repository = tmp_path / "untrusted-project"
    repository.mkdir()
    write_project(
        repository,
        provider="codex-cli",
        max_file_bytes=512 * 1024 * 1024,
        max_task_bytes=2 * 1024 * 1024 * 1024,
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler_factory(
            queue=TaskQueue(tmp_path / "queue.db"),
            runs_dir=tmp_path / "runs",
            auth_token="untrusted-token",
            webhook_secret="",
            default_repository=repository,
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config = json_request(
            f"http://127.0.0.1:{server.server_port}/config", "untrusted-token"
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert config["project_config_trusted"] is False
    assert config["runtime_provider"] == "codex-sdk"
    assert config["attachments"]["max_file_bytes"] == 100 * 1024 * 1024  # type: ignore[index]
    assert config["attachments"]["max_task_bytes"] == 500 * 1024 * 1024  # type: ignore[index]


def test_control_plane_maintenance_removes_expired_unbound_uploads(
    tmp_path: Path,
) -> None:
    attachment_store_root = tmp_path / "custom-home" / ".agent-uploads"
    store = AttachmentStore(attachment_store_root)
    staged = store.stage(
        [IncomingAttachment("context.txt", io.BytesIO(b"context"), "text/plain")]
    )
    created = float(staged.manifest["created_at_epoch"])

    removed = cleanup_attachment_uploads(
        attachment_store_root,
        now=created + store.limits.ttl_seconds + 1,
    )

    assert removed == (staged.set_id,)
    assert not staged.root.exists()


def test_control_plane_without_token_is_read_only(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler_factory(
            queue=TaskQueue(tmp_path / "queue.db"),
            runs_dir=tmp_path / "runs",
            attachment_store_root=tmp_path / "uploads",
            auth_token="",
            webhook_secret="",
            default_repository=repository,
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        query = urlencode({"name": "csrf.txt", "repository": str(repository)})
        request = Request(
            f"http://127.0.0.1:{server.server_port}/ui/attachments?{query}",
            data=b"untrusted cross-origin body",
            method="POST",
            headers={"Content-Type": "text/plain"},
        )
        with pytest.raises(HTTPError) as rejected:
            urlopen(request, timeout=5)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert rejected.value.code == 403
    assert not (tmp_path / "uploads").exists()


def test_custom_runs_dir_keeps_upload_store_aligned_with_agent_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs_dir = tmp_path / "external-state" / "custom-runs"
    harness_home = tmp_path / "installed-harness"
    attachment_store_root = harness_home / ".agent-uploads"
    handler_type = handler_factory(
        queue=TaskQueue(tmp_path / "queue.db"),
        runs_dir=runs_dir,
        attachment_store_root=attachment_store_root,
        auth_token="",
        webhook_secret="",
        default_repository=tmp_path,
    )
    handler = object.__new__(handler_type)
    observed: dict[str, object] = {}

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps({"status": "queued", "task_id": "aligned"}),
            stderr="",
        )

    monkeypatch.setattr("control_plane_api.subprocess.run", run)

    store = handler.attachment_store()
    result = handler.agent_command(tmp_path, ["task", "Use staged context"])

    assert handler.runs_dir == runs_dir
    assert store.staging_root == attachment_store_root.absolute()
    assert observed["env"]["AI_HARNESS_HOME"] == str(harness_home.resolve())  # type: ignore[index]
    assert result == {"status": "queued", "task_id": "aligned"}


def test_server_cleanup_uses_handler_attachment_store_not_runs_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runs_dir = tmp_path / "external-state" / "custom-runs"
    attachment_store_root = tmp_path / "installed-harness" / ".agent-uploads"
    cleanup_started = threading.Event()
    observed: dict[str, object] = {}

    class Server:
        server_port = 8765

        def __init__(self, address: tuple[str, int], handler: object) -> None:
            observed["address"] = address
            observed["handler"] = handler

        def serve_forever(self) -> None:
            assert cleanup_started.wait(timeout=2)

        def server_close(self) -> None:
            observed["closed"] = True

    def cleanup(root: Path, _stop: threading.Event) -> None:
        observed["cleanup_root"] = root
        cleanup_started.set()

    monkeypatch.setattr("control_plane_api.ThreadingHTTPServer", Server)
    monkeypatch.setattr("control_plane_api.attachment_cleanup_loop", cleanup)

    serve_control_plane(
        host="127.0.0.1",
        port=0,
        db_path=tmp_path / "queue.db",
        runs_dir=runs_dir,
        attachment_store_root=attachment_store_root,
        auth_token="",
    )

    handler_type = observed["handler"]
    assert issubclass(handler_type, ControlPlaneHandler)  # type: ignore[arg-type]
    assert handler_type.runs_dir == runs_dir.resolve()  # type: ignore[union-attr]
    assert handler_type.attachment_store_root == attachment_store_root.resolve()  # type: ignore[union-attr]
    assert handler_type.cli_mutations_enabled is False  # type: ignore[union-attr]
    assert observed["cleanup_root"] == attachment_store_root.resolve()
    assert observed["closed"] is True


def test_server_enables_cli_mutations_for_canonical_installed_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness_home = tmp_path / "installed-harness"
    attachment_store_root = harness_home / ".agent-uploads"
    observed: dict[str, object] = {}

    class Server:
        server_port = 8765

        def __init__(self, _address: tuple[str, int], handler: object) -> None:
            observed["handler"] = handler

        def serve_forever(self) -> None:
            return

        def server_close(self) -> None:
            return

    monkeypatch.setattr("control_plane_api.ThreadingHTTPServer", Server)
    monkeypatch.setattr(
        "control_plane_api.attachment_cleanup_loop",
        lambda _root, _stop: None,
    )

    serve_control_plane(
        host="127.0.0.1",
        port=0,
        db_path=harness_home / ".agent-queue" / "tasks.db",
        runs_dir=harness_home / ".agent-runs",
        attachment_store_root=attachment_store_root,
        auth_token="",
    )

    handler_type = observed["handler"]
    assert handler_type.cli_mutations_enabled is True  # type: ignore[union-attr]


def test_misaligned_control_plane_rejects_only_cli_backed_mutations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    calls: list[list[str]] = []

    def command(
        _handler: object,
        _repository: Path,
        arguments: list[str],
        *,
        timeout: int = 120,
    ) -> dict[str, object]:
        del timeout
        calls.append(arguments)
        return {"status": "queued"}

    monkeypatch.setattr(ControlPlaneHandler, "agent_command", command)
    queue = TaskQueue(tmp_path / "custom-queue.db")
    token = "misaligned-control-plane-token"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler_factory(
            queue=queue,
            runs_dir=tmp_path / "custom-runs",
            attachment_store_root=tmp_path / "installed-harness" / ".agent-uploads",
            cli_mutations_enabled=False,
            auth_token=token,
            webhook_secret="",
            default_repository=repository,
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        attachment_query = urlencode(
            {"name": "context.txt", "repository": str(repository)}
        )
        attachment_request = Request(
            f"{base}/ui/attachments?{attachment_query}",
            data=b"context",
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "text/plain",
            },
        )
        with pytest.raises(HTTPError) as rejected_attachment:
            urlopen(attachment_request, timeout=5)
        assert rejected_attachment.value.code == 409

        for path, body in (
            ("/ui/tasks", {"goal": "must not launch"}),
            ("/ui/tasks/batch", {}),
            ("/tasks/batch", {}),
            ("/ui/runs/run-1/approve", {}),
            ("/ui/runs/run-1/answer", {"response": "yes"}),
            ("/ui/runs/run-1/retry", {}),
            ("/ui/runs/run-1/abort", {}),
        ):
            with pytest.raises(HTTPError) as rejected:
                json_request(base + path, token, method="POST", body=body)
            assert rejected.value.code == 409

        direct = json_request(
            f"{base}/tasks",
            token,
            method="POST",
            body={
                "source": "api",
                "repository": str(repository),
                "payload": {
                    "external_id": "direct-1",
                    "task_id": "direct-1",
                    "goal": "direct queue intake remains available",
                },
            },
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert direct["queue_task"]["payload"]["task_id"] == "direct-1"  # type: ignore[index]
    assert calls == []

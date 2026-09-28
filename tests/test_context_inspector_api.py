from __future__ import annotations

import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from ai_harness.project import default_config, trust_key, write_project_config  # noqa: E402
from context_inspector import preview_sources  # noqa: E402
from control_plane_api import handler_factory  # noqa: E402
from task_queue import TaskQueue  # noqa: E402


def test_read_only_server_withholds_private_context(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        handler_factory(
            queue=TaskQueue(tmp_path / "queue.db"),
            runs_dir=tmp_path,
            auth_token="",
            webhook_secret="",
        ),
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        for suffix in ("/runs/run/context", "/runs/run/context/" + "a" * 64):
            for headers in ({}, {"Authorization": "Bearer arbitrary-client-value"}):
                request = Request(
                    f"http://127.0.0.1:{server.server_port}{suffix}", headers=headers
                )
                with pytest.raises(HTTPError) as failure:
                    urlopen(request, timeout=5)
                assert failure.value.code == 403
                assert "bearer token is required" in failure.value.read().decode()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def test_preview_uses_repository_bound_private_memory(tmp_path: Path) -> None:
    control = tmp_path / "control"
    control.mkdir()
    (control / ".agent-project-profiles.yaml").write_text("profiles: {}\n")
    repositories = [tmp_path / "first" / "shared", tmp_path / "second" / "shared"]
    for index, repo in enumerate(repositories):
        repo.mkdir(parents=True)
        (repo / "README.md").write_text("# Architecture\nProject architecture.\n")
        config_dir = repo / ".agent"
        config_dir.mkdir()
        # Both projects intentionally share their display identity.
        write_project_config(default_config(repo, profile="nextjs_web"))
        private = control / "docs" / "projects" / "by-key" / trust_key(repo)
        (private / "wiki").mkdir(parents=True)
        (private / "privacy.md").write_text("Private project architecture memory.\n")
        (private / "wiki" / "architecture.md").write_text(
            f"# Architecture\nONLY_PROJECT_{index}_ARCHITECTURE\n"
        )
    for index, repo in enumerate(repositories):
        result = preview_sources(
            repository=repo, goal="Project architecture", role="planner", control_root=control
        )
        encoded = json.dumps(result)
        assert f"ONLY_PROJECT_{index}_ARCHITECTURE" in encoded
        assert f"ONLY_PROJECT_{1 - index}_ARCHITECTURE" not in encoded

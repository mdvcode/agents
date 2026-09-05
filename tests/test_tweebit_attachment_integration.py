from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from ai_harness.attachments import AttachmentStore, IncomingAttachment
from ai_harness.cli import (
    CLIError,
    bind_task_attachments,
    finalize_bound_task_attachments,
    restore_bound_task_attachments,
)


def stage_text(store: AttachmentStore, name: str, content: str) -> str:
    staged = store.stage(
        [
            IncomingAttachment(
                filename=name,
                stream=io.BytesIO(content.encode("utf-8")),
                declared_mime="text/plain",
            )
        ]
    )
    return staged.set_id


def test_bind_task_attachments_combines_sets_and_pins_manifest(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / ".agent-uploads")
    first = stage_text(store, "brief.md", "Product requirements")
    second = stage_text(store, "notes.txt", "Review notes")

    result = bind_task_attachments(
        root=tmp_path,
        run_id="run-with-inputs",
        set_ids=[first, second],
        runtime_provider="codex-sdk",
        runtime_consent=True,
    )

    manifest_path = Path(result["input_manifest"])
    assert manifest_path == tmp_path / ".agent-runs" / "run-with-inputs" / "inputs" / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    assert result["input_manifest_sha256"] == hashlib.sha256(manifest_bytes).hexdigest()
    assert result["attachment_count"] == 2
    assert result["attachment_status"] == "complete"
    manifest = json.loads(manifest_bytes)
    assert [item["safe_name"] for item in manifest["attachments"]] == [
        "brief.md",
        "notes.txt",
    ]
    assert "Product requirements" not in manifest_path.read_text(encoding="utf-8")
    # Combining is deliberately non-destructive. The immutable combined set is
    # bound to the run while original uploads remain available for rollback/TTL.
    assert (tmp_path / ".agent-uploads" / first).is_dir()
    assert (tmp_path / ".agent-uploads" / second).is_dir()


def test_bind_task_attachments_requires_explicit_runtime_consent(tmp_path: Path) -> None:
    store = AttachmentStore(tmp_path / ".agent-uploads")
    set_id = stage_text(store, "private.txt", "private context")

    with pytest.raises(CLIError, match="explicit consent"):
        bind_task_attachments(
            root=tmp_path,
            run_id="run-without-consent",
            set_ids=[set_id],
            runtime_provider="codex-sdk",
            runtime_consent=False,
        )

    assert (tmp_path / ".agent-uploads" / set_id).is_dir()
    assert not (tmp_path / ".agent-runs" / "run-without-consent").exists()


def test_bind_rejects_preexisting_incomplete_set_without_consuming_it(
    tmp_path: Path,
) -> None:
    store = AttachmentStore(tmp_path / ".agent-uploads")
    set_id = stage_text(store, "legacy.txt", "legacy partial context")
    manifest_path = tmp_path / ".agent-uploads" / set_id / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "partial"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o600)

    with pytest.raises(CLIError, match="processing_incomplete"):
        bind_task_attachments(
            root=tmp_path,
            run_id="run-incomplete",
            set_ids=[set_id],
            runtime_provider="codex-sdk",
            runtime_consent=True,
        )

    assert (tmp_path / ".agent-uploads" / set_id).is_dir()
    assert not (tmp_path / ".agent-runs" / "run-incomplete" / "inputs").exists()


def test_combined_binding_has_lossless_rollback_and_cleans_sources_on_success(
    tmp_path: Path,
) -> None:
    store = AttachmentStore(tmp_path / ".agent-uploads")
    first = stage_text(store, "first.txt", "first")
    second = stage_text(store, "second.txt", "second")
    failed = bind_task_attachments(
        root=tmp_path,
        run_id="failed-intake",
        set_ids=[first, second],
        runtime_provider="codex-sdk",
        runtime_consent=True,
    )

    assert restore_bound_task_attachments(root=tmp_path, attachment_input=failed) == ""
    assert (tmp_path / ".agent-uploads" / first).is_dir()
    assert (tmp_path / ".agent-uploads" / second).is_dir()
    assert not Path(failed["run_inputs"]).exists()
    assert not (tmp_path / ".agent-uploads" / failed["attachment_set_id"]).exists()

    succeeded = bind_task_attachments(
        root=tmp_path,
        run_id="successful-intake",
        set_ids=[first, second],
        runtime_provider="codex-sdk",
        runtime_consent=True,
    )
    assert finalize_bound_task_attachments(
        root=tmp_path, attachment_input=succeeded
    ) == ""
    assert Path(succeeded["run_inputs"]).is_dir()
    assert not (tmp_path / ".agent-uploads" / first).exists()
    assert not (tmp_path / ".agent-uploads" / second).exists()

"""Immutable, verifiable records of the exact input supplied by Harness."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .cache import fingerprint_text
from .content_guard import (
    ContextGuardError,
    ContextPrivacyPolicy,
    redact_value,
    require_safe_value,
)
from .models import PrivacyClass


MAX_SNAPSHOT_BYTES = 4_000_000


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def write_private_text(path: Path, text: str) -> None:
    confined_path(path.parent, path.name)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(prefix=".context-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def confined_path(root: Path, *parts: str) -> Path:
    """Reject symlinks and traversal before using private context evidence."""
    root = root.absolute()
    if any(part in {"", ".", ".."} or "/" in part or "\\" in part for part in parts):
        raise ContextGuardError("Invalid context evidence path")
    path = root.joinpath(*parts)
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ContextGuardError("Context evidence cannot use symbolic links")
    return path


def write_private_json(
    path: Path, value: dict[str, Any], *, immutable: bool = False
) -> None:
    confined_path(path.parent, path.name)
    encoded = canonical_json(value).encode("utf-8")
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise ContextGuardError("Context snapshot exceeds the local size limit")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary_name = tempfile.mkstemp(prefix=".context-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if immutable:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.stat().st_size > MAX_SNAPSHOT_BYTES:
                    raise ContextGuardError(
                        "Existing context snapshot exceeds the local size limit"
                    )
                existing = (
                    read_snapshot(path)
                    if "payload" in value
                    else json.loads(path.read_text(encoding="utf-8"))
                )
                matches = (
                    existing.get("payload") == value.get("payload")
                    if "payload" in value
                    else existing == value
                )
                if not matches:
                    raise ContextGuardError(
                        "Existing context snapshot does not match its digest"
                    )
        else:
            temporary.replace(path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def read_snapshot(path: Path) -> dict[str, Any]:
    confined_path(path.parent, path.name)
    if path.stat().st_size > MAX_SNAPSHOT_BYTES:
        raise ContextGuardError("Context snapshot exceeds the local size limit")
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or not isinstance(value.get("payload"), dict)
    ):
        raise ContextGuardError("Unsupported context snapshot")
    payload = value["payload"]
    for key in (
        "role",
        "run_id",
        "phase",
        "status",
        "scope",
        "created_at",
        "effective_context_digest",
        "prompt_digest",
    ):
        if not isinstance(value.get(key), str):
            raise ContextGuardError("Invalid context snapshot metadata")
    for key in (
        "prompt",
        "runtime",
        "sandbox",
        "repository",
        "thread_id",
        "run_id",
        "role",
    ):
        if not isinstance(payload.get(key), str):
            raise ContextGuardError("Invalid context payload")
    if not isinstance(payload.get("output_schema"), dict) or not isinstance(
        payload.get("settings"), dict
    ):
        raise ContextGuardError("Invalid context execution contract")
    if not isinstance(value.get("included"), list) or not isinstance(
        value.get("excluded"), list
    ):
        raise ContextGuardError("Invalid context provenance")
    if path.stem != value["effective_context_digest"].removeprefix("sha256:"):
        raise ContextGuardError("Context snapshot filename does not match its digest")
    envelope = {key: item for key, item in value.items() if key != "snapshot_digest"}
    if fingerprint_text(canonical_json(envelope)) != value.get("snapshot_digest"):
        raise ContextGuardError("Context snapshot provenance integrity check failed")
    if fingerprint_text(canonical_json(payload)) != value.get(
        "effective_context_digest"
    ):
        raise ContextGuardError("Context snapshot integrity check failed")
    if fingerprint_text(str(payload.get("prompt", ""))) != value.get("prompt_digest"):
        raise ContextGuardError("Context prompt integrity check failed")
    require_safe_value(value, "Stored context")
    return value


def record_payload(
    *,
    request: dict[str, Any],
    manifest: dict[str, Any],
    prompt: str,
    output_schema: dict[str, Any],
    runtime: str,
    settings: dict[str, Any],
    sandbox: str,
    thread_id: str = "",
    phase: str = "role",
    control_root: Path,
) -> dict[str, Any]:
    """Scan and freeze immediately before submission, including repair turns.

    The digest covers prompt, output schema, destination and execution settings.
    Runtime-owned system instructions, earlier turns and tool reads are outside
    this new-input snapshot. Recording is not proof the provider accepted a turn.
    """
    policy = ContextPrivacyPolicy.load(control_root, str(manifest.get("project", "")))
    # Task descriptions and role contracts are private even if all references
    # happen to be public; adding a provider never grants it implicit access.
    if runtime not in policy.private_destinations:
        raise ContextGuardError(
            "Runtime destination is not allowed for project-private task input"
        )
    for item in manifest.get("selected_context", []):
        if isinstance(item, dict) and item.get("privacy") in {
            PrivacyClass.LOCAL_ONLY.value,
            PrivacyClass.SECRET_NEVER_MODEL.value,
        }:
            raise ContextGuardError(
                "Compiled context contains a prohibited source; rebuild it"
            )
    payload = {
        "prompt": prompt,
        "output_schema": output_schema,
        "runtime": runtime,
        "settings": settings,
        "sandbox": sandbox,
        "repository": str(request.get("repository", "")),
        "thread_id": thread_id,
        "run_id": str(request.get("run_id", "")),
        "role": str(request.get("role", "")),
    }
    require_safe_value(payload, "Final runtime input")
    digest = fingerprint_text(canonical_json(payload))
    value = {
        "version": 1,
        "effective_context_digest": digest,
        "prompt_digest": fingerprint_text(prompt),
        "context_package_digest": manifest.get(
            "context_package_digest", manifest.get("effective_context_digest", "")
        ),
        "context_revision": manifest.get("context_revision", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "run_id": str(request.get("run_id", "")),
        "role": str(request.get("role", "")),
        "phase": phase,
        "status": "prepared_for_submission",
        "scope": "harness_turn_input",
        "limitations": [
            "Runtime system instructions, previous session turns and subsequent tool reads are not part of this snapshot."
        ],
        "payload": payload,
        "included": manifest.get("selected_context", []) if phase == "role" else [],
        "excluded": manifest.get("excluded_context", []) if phase == "role" else [],
        "input_kind": "role_context" if phase == "role" else "structured_output_repair",
    }
    value = redact_value(value)
    # Redaction must never silently alter input after its digest is computed.
    if value["payload"] != payload:
        raise ContextGuardError("Final input changed during privacy validation")
    value["snapshot_digest"] = fingerprint_text(canonical_json(value))
    run_dir = Path(str(request["artifacts_dir"])).absolute().parent
    directory = confined_path(run_dir, "context-manifests", "effective")
    path = confined_path(directory, digest.removeprefix("sha256:") + ".json")
    write_private_json(path, value, immutable=True)
    stored = read_snapshot(path)
    event = {
        "time": datetime.now(timezone.utc).isoformat(),
        "role": value["role"],
        "phase": phase,
        "runtime": runtime,
        "effective_context_digest": digest,
        "status": "prepared_for_submission",
    }
    event_path = confined_path(directory, "submissions.jsonl")
    fd = os.open(
        event_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(canonical_json(event) + "\n")
    return stored

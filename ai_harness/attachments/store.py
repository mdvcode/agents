from __future__ import annotations

import codecs
import contextlib
import datetime as dt
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import threading
import unicodedata
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from .models import (
    MIB,
    MAX_PENDING_ATTACHMENT_BYTES,
    MAX_PENDING_ATTACHMENT_SETS,
    AttachmentLimitError,
    AttachmentLimits,
    AttachmentQuotaError,
    AttachmentRejected,
    AttachmentSet,
    AttachmentStorageError,
    IncomingAttachment,
)
from .pdf import PDFProcessor

try:
    import fcntl
except ImportError:  # pragma: no cover - AI Harness currently targets Unix hosts.
    fcntl = None  # type: ignore[assignment]


_SET_ID = re.compile(r"^[0-9a-f]{32}$")
_PENDING_ID = re.compile(r"^\.pending-([0-9a-f]{32})$")
_WINDOWS_RESERVED = re.compile(
    r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", re.IGNORECASE
)
_SAFE_NAME_CHAR = re.compile(r"[^A-Za-z0-9._-]+")
_QUOTA_THREAD_LOCK = threading.Lock()

_TEXT_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
    ".jsonl": "application/x-ndjson",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".toml": "application/toml",
    ".xml": "application/xml",
    ".html": "text/html",
    ".htm": "text/html",
    ".css": "text/css",
    ".js": "text/javascript",
    ".jsx": "text/jsx",
    ".ts": "text/typescript",
    ".tsx": "text/tsx",
    ".py": "text/x-python",
    ".sql": "application/sql",
    ".ini": "text/plain",
    ".cfg": "text/plain",
    ".log": "text/plain",
}
_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
}
_ARCHIVE_EXTENSIONS = {
    ".7z",
    ".apk",
    ".bz2",
    ".dmg",
    ".docx",
    ".gz",
    ".iso",
    ".jar",
    ".ods",
    ".odt",
    ".pptx",
    ".rar",
    ".tar",
    ".tgz",
    ".war",
    ".xlsx",
    ".xz",
    ".zip",
}
_EXECUTABLE_EXTENSIONS = {
    ".app",
    ".bat",
    ".bin",
    ".class",
    ".cmd",
    ".com",
    ".dll",
    ".dylib",
    ".exe",
    ".msi",
    ".ps1",
    ".so",
    ".wasm",
}
_ACTIVE_IMAGE_EXTENSIONS = {".svg", ".svgz"}


def _safe_name(filename: str) -> str:
    if not isinstance(filename, str) or not filename:
        raise AttachmentRejected("invalid_filename")
    if len(filename.encode("utf-8", errors="ignore")) > 1024:
        raise AttachmentRejected("filename_too_long")
    normalized = unicodedata.normalize("NFKC", filename)
    if normalized in {".", ".."} or "/" in normalized or "\\" in normalized:
        raise AttachmentRejected("path_traversal_filename")
    if normalized.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", normalized):
        raise AttachmentRejected("absolute_filename")
    if any(
        ord(character) < 32
        or ord(character) == 127
        or unicodedata.category(character) in {"Cf", "Cs"}
        for character in normalized
    ):
        raise AttachmentRejected("unsafe_filename_characters")
    extension = Path(normalized).suffix.lower()
    base = normalized[: -len(extension)] if extension else normalized
    base = _SAFE_NAME_CHAR.sub("_", base).strip("._-")
    # Keep storage paths deliberately ASCII-only, but do not reject an otherwise
    # valid user file merely because its display name is written in Cyrillic or
    # another non-ASCII script. The original browser-side name is never trusted
    # as a path; a neutral basename is sufficient because every stored object is
    # already namespaced by its index and random attachment id.
    if not base:
        base = "attachment"
    if _WINDOWS_RESERVED.match(base):
        base = f"attachment_{base}"
    max_base = max(1, 120 - len(extension))
    safe = f"{base[:max_base]}{extension}"
    if _WINDOWS_RESERVED.match(safe):
        raise AttachmentRejected("unsafe_filename")
    return safe


def _expected_media(safe_name: str) -> tuple[str, str]:
    extension = Path(safe_name).suffix.lower()
    if extension in _ARCHIVE_EXTENSIONS:
        raise AttachmentRejected("archive_attachments_are_not_allowed")
    if extension in _EXECUTABLE_EXTENSIONS:
        raise AttachmentRejected("executable_attachments_are_not_allowed")
    if extension in _ACTIVE_IMAGE_EXTENSIONS:
        raise AttachmentRejected("active_image_formats_are_not_allowed")
    if extension == ".pdf":
        return "pdf", "application/pdf"
    if extension in _IMAGE_TYPES:
        return "image", _IMAGE_TYPES[extension]
    if extension in _TEXT_TYPES:
        return "text", _TEXT_TYPES[extension]
    raise AttachmentRejected("unsupported_attachment_type")


def _binary_signature(header: bytes) -> str | None:
    archive_signatures = (
        b"PK\x03\x04",
        b"PK\x05\x06",
        b"PK\x07\x08",
        b"\x1f\x8b",
        b"BZh",
        b"\xfd7zXZ\x00",
        b"7z\xbc\xaf\x27\x1c",
        b"Rar!\x1a\x07",
    )
    if header.startswith(archive_signatures) or header[257:262] == b"ustar":
        return "archive"
    executable_signatures = (
        b"\x7fELF",
        b"MZ",
        b"\x00asm",
        b"\xca\xfe\xba\xbe",
        b"\xfe\xed\xfa\xce",
        b"\xce\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xcf\xfa\xed\xfe",
        b"\xca\xfe\xba\xbf",
        b"\xbf\xba\xfe\xca",
    )
    if header.startswith(executable_signatures) or header.startswith(b"#!"):
        return "executable"
    if header.startswith(b"%PDF-"):
        return "pdf"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def _validate_declared_mime(declared: str | None, actual: str) -> None:
    if declared is None or not declared.strip():
        return
    if len(declared) > 200:
        raise AttachmentRejected("invalid_declared_mime")
    value = declared.partition(";")[0].strip().lower()
    accepted = {actual}
    if actual.startswith("text/") or actual in {
        "application/json",
        "application/x-ndjson",
        "application/yaml",
        "application/toml",
        "application/xml",
        "application/sql",
    }:
        accepted.add("text/plain")
    if actual == "text/markdown":
        accepted.add("text/x-markdown")
    if actual == "application/yaml":
        accepted.update({"text/yaml", "application/x-yaml"})
    if actual == "image/jpeg":
        accepted.add("image/jpg")
    if value not in accepted:
        raise AttachmentRejected("declared_mime_mismatch")


def _validate_text(path: Path) -> None:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    try:
        with path.open("rb") as handle:
            first = handle.read(2)
            if first == b"#!":
                raise AttachmentRejected("executable_attachments_are_not_allowed")
            handle.seek(0)
            for chunk in iter(lambda: handle.read(MIB), b""):
                value = decoder.decode(chunk, final=False)
                for character in value:
                    if ord(character) < 32 and character not in "\n\r\t\f\b":
                        raise AttachmentRejected("binary_content_in_text_attachment")
            tail = decoder.decode(b"", final=True)
            if any(
                ord(character) < 32 and character not in "\n\r\t\f\b"
                for character in tail
            ):
                raise AttachmentRejected("binary_content_in_text_attachment")
    except UnicodeDecodeError as exc:
        raise AttachmentRejected("invalid_utf8_text_attachment") from exc


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
        if (
            len(header) != 24
            or header[:8] != b"\x89PNG\r\n\x1a\n"
            or header[12:16] != b"IHDR"
            or int.from_bytes(header[8:12], "big") != 13
        ):
            raise AttachmentRejected("malformed_png")
        width = int.from_bytes(header[16:20], "big")
        height = int.from_bytes(header[20:24], "big")
        handle.seek(-12, os.SEEK_END)
        trailer = handle.read(12)
    if trailer[:4] != b"\x00\x00\x00\x00" or trailer[4:8] != b"IEND":
        raise AttachmentRejected("malformed_png")
    return width, height


def _jpeg_dimensions(path: Path) -> tuple[int, int]:
    start_of_frame = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    with path.open("rb") as handle:
        if handle.read(2) != b"\xff\xd8":
            raise AttachmentRejected("malformed_jpeg")
        width = height = 0
        while handle.tell() < MIB:
            marker_start = handle.read(1)
            if not marker_start:
                break
            if marker_start != b"\xff":
                continue
            marker = handle.read(1)
            while marker == b"\xff":
                marker = handle.read(1)
            if not marker:
                break
            marker_value = marker[0]
            if marker_value in {0xD8, 0xD9}:
                continue
            if marker_value == 0xDA:
                break
            length_bytes = handle.read(2)
            if len(length_bytes) != 2:
                break
            segment_length = int.from_bytes(length_bytes, "big")
            if segment_length < 2:
                break
            if marker_value in start_of_frame:
                frame = handle.read(5)
                if len(frame) != 5:
                    break
                height = int.from_bytes(frame[1:3], "big")
                width = int.from_bytes(frame[3:5], "big")
                break
            handle.seek(segment_length - 2, os.SEEK_CUR)
        handle.seek(-2, os.SEEK_END)
        trailer = handle.read(2)
    if width < 1 or height < 1 or trailer != b"\xff\xd9":
        raise AttachmentRejected("malformed_jpeg")
    return width, height


def _gif_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(10)
        handle.seek(-1, os.SEEK_END)
        trailer = handle.read(1)
    if len(header) != 10 or header[:6] not in {b"GIF87a", b"GIF89a"} or trailer != b";":
        raise AttachmentRejected("malformed_gif")
    return int.from_bytes(header[6:8], "little"), int.from_bytes(
        header[8:10], "little"
    )


def _validate_image(path: Path, media_type: str, max_pixels: int) -> tuple[int, int]:
    if media_type == "image/png":
        width, height = _png_dimensions(path)
    elif media_type == "image/jpeg":
        width, height = _jpeg_dimensions(path)
    elif media_type == "image/gif":
        width, height = _gif_dimensions(path)
    else:
        raise AttachmentRejected("unsupported_image_type")
    if width < 1 or height < 1 or width * height > max_pixels:
        raise AttachmentRejected("image_dimensions_exceed_limit")
    return width, height


def _validate_pdf_container(path: Path) -> None:
    with path.open("rb") as handle:
        if handle.read(8)[:5] != b"%PDF-":
            raise AttachmentRejected("pdf_mime_spoof")
        handle.seek(max(0, path.stat().st_size - 4096))
        tail = handle.read()
    marker = tail.rfind(b"%%EOF")
    if marker < 0 or tail[marker + 5 :].strip(b"\x00\t\n\r\f "):
        raise AttachmentRejected("malformed_pdf")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(MIB), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    temporary = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(temporary, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _read_private_json(path: Path, max_bytes: int = 2 * MIB) -> dict[str, Any]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AttachmentStorageError("attachment_manifest_unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
            raise AttachmentStorageError("invalid_attachment_manifest")
        with os.fdopen(descriptor, "r", encoding="utf-8", closefd=False) as handle:
            value = json.load(handle)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AttachmentStorageError("invalid_attachment_manifest") from exc
    finally:
        os.close(descriptor)
    if not isinstance(value, dict):
        raise AttachmentStorageError("invalid_attachment_manifest")
    return value


class AttachmentStore:
    """Private staging, validation, manifests, binding, and TTL cleanup."""

    def __init__(
        self,
        staging_root: Path,
        *,
        limits: AttachmentLimits | None = None,
        pdf_processor: PDFProcessor | None = None,
    ) -> None:
        self.staging_root = Path(staging_root).absolute()
        self.limits = limits or AttachmentLimits()
        self.pdf_processor = pdf_processor or PDFProcessor()
        self._ensure_private_root()

    @contextlib.contextmanager
    def _quota_lock(self) -> Iterable[None]:
        lock_path = self.staging_root / ".quota.lock"
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        with _QUOTA_THREAD_LOCK:
            try:
                descriptor = os.open(lock_path, flags, 0o600)
            except OSError as exc:
                raise AttachmentStorageError("attachment quota lock unavailable") from exc
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
                    raise AttachmentStorageError("attachment quota lock is unsafe")
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def _pending_usage(self) -> tuple[int, int]:
        sets = 0
        total = 0
        with os.scandir(self.staging_root) as entries:
            roots = [
                self.staging_root / entry.name
                for entry in entries
                if _SET_ID.fullmatch(entry.name) or _PENDING_ID.fullmatch(entry.name)
            ]
        for root in roots:
            sets += 1
            stack = [root]
            while stack:
                path = stack.pop()
                try:
                    metadata = path.lstat()
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(metadata.st_mode):
                    continue
                if stat.S_ISREG(metadata.st_mode):
                    total += metadata.st_size
                    continue
                if not stat.S_ISDIR(metadata.st_mode):
                    continue
                try:
                    with os.scandir(path) as children:
                        stack.extend(path / child.name for child in children)
                except FileNotFoundError:
                    continue
        return sets, total

    def stage(self, attachments: Iterable[IncomingAttachment]) -> AttachmentSet:
        with self._quota_lock():
            set_count, pending_bytes = self._pending_usage()
            if set_count >= MAX_PENDING_ATTACHMENT_SETS:
                raise AttachmentQuotaError("pending_attachment_set_quota_exceeded")
            if pending_bytes >= MAX_PENDING_ATTACHMENT_BYTES:
                raise AttachmentQuotaError("pending_attachment_byte_quota_exceeded")
            staged = self._stage_unlocked(attachments)
            set_count, pending_bytes = self._pending_usage()
            if (
                set_count > MAX_PENDING_ATTACHMENT_SETS
                or pending_bytes > MAX_PENDING_ATTACHMENT_BYTES
            ):
                self._remove_entry(staged.root)
                _fsync_directory(self.staging_root)
                raise AttachmentQuotaError("pending_attachment_byte_quota_exceeded")
            return staged

    def _stage_unlocked(self, attachments: Iterable[IncomingAttachment]) -> AttachmentSet:
        items = self._bounded_items(attachments)
        set_id = uuid.uuid4().hex
        pending = self.staging_root / f".pending-{set_id}"
        completed = self.staging_root / set_id
        pending.mkdir(mode=0o700)
        (pending / "files").mkdir(mode=0o700)
        (pending / "derived").mkdir(mode=0o700)
        total_bytes = 0
        derived_bytes = 0
        descriptors: list[dict[str, Any]] = []
        overall_status = "complete"
        try:
            for index, incoming in enumerate(items, start=1):
                if not isinstance(incoming, IncomingAttachment):
                    raise AttachmentRejected("invalid_attachment_input")
                safe_name = _safe_name(incoming.filename)
                kind, media_type = _expected_media(safe_name)
                attachment_id = uuid.uuid4().hex
                stored_name = f"{index:02d}-{attachment_id[:12]}-{safe_name}"
                relative_path = PurePosixPath("files") / stored_name
                destination = pending / Path(relative_path.as_posix())
                size, sha256 = self._copy_stream(
                    incoming.stream,
                    destination,
                    task_bytes_before=total_bytes,
                )
                total_bytes += size
                dimensions: tuple[int, int] | None = None
                with destination.open("rb") as handle:
                    header = handle.read(512)
                signature = _binary_signature(header)
                if signature in {"archive", "executable"}:
                    raise AttachmentRejected(f"{signature}_content_is_not_allowed")
                if kind == "text":
                    if signature is not None:
                        raise AttachmentRejected("text_mime_spoof")
                    _validate_text(destination)
                elif kind == "image":
                    if signature != media_type:
                        raise AttachmentRejected("image_mime_spoof")
                    if size > self.limits.max_runtime_image_bytes:
                        raise AttachmentLimitError("image_exceeds_runtime_payload_limit")
                    dimensions = _validate_image(
                        destination, media_type, self.limits.max_image_pixels
                    )
                elif kind == "pdf":
                    if signature != "pdf":
                        raise AttachmentRejected("pdf_mime_spoof")
                    _validate_pdf_container(destination)
                _validate_declared_mime(incoming.declared_mime, media_type)

                descriptor: dict[str, Any] = {
                    "id": attachment_id,
                    "safe_name": safe_name,
                    "path": relative_path.as_posix(),
                    "kind": kind,
                    "media_type": media_type,
                    "size": size,
                    "sha256": sha256,
                }
                if dimensions is not None:
                    descriptor["width"], descriptor["height"] = dimensions
                if kind == "text":
                    descriptor["content"] = [
                        self._source_content_descriptor(descriptor, "text")
                    ]
                elif kind == "image":
                    descriptor["content"] = [
                        self._source_content_descriptor(descriptor, "local_image")
                    ]
                else:
                    relative_output = PurePosixPath("derived") / attachment_id
                    pdf = self.pdf_processor.process(
                        destination,
                        pending / Path(relative_output.as_posix()),
                        relative_output_dir=relative_output,
                        attachment_id=attachment_id,
                        source_sha256=sha256,
                    )
                    descriptor["pdf"] = pdf
                    descriptor["content"] = [
                        page["descriptor"]
                        for page in pdf["pages"]
                        if isinstance(page, dict) and "descriptor" in page
                    ]
                    derived_bytes += int(pdf["output_bytes"])
                    if pdf["status"] != "complete":
                        overall_status = "partial"
                descriptors.append(descriptor)

            created_at = dt.datetime.now(dt.timezone.utc)
            manifest = {
                "version": 1,
                "set_id": set_id,
                "status": overall_status,
                "created_at": created_at.isoformat(),
                "created_at_epoch": created_at.timestamp(),
                "attachment_count": len(descriptors),
                "total_bytes": total_bytes,
                "derived_bytes": derived_bytes,
                "attachments": descriptors,
            }
            _atomic_json(pending / "manifest.json", manifest)
            os.rename(pending, completed)
            _fsync_directory(self.staging_root)
            return AttachmentSet(set_id=set_id, root=completed, manifest=manifest)
        except BaseException:
            self._remove_entry(pending)
            raise

    def stage_paths(
        self,
        paths: Iterable[Path],
        *,
        declared_mime_by_name: Mapping[str, str] | None = None,
    ) -> AttachmentSet:
        selected = self._bounded_items(Path(path) for path in paths)
        incoming: list[IncomingAttachment] = []
        with contextlib.ExitStack() as stack:
            for path in selected:
                try:
                    before = path.lstat()
                except OSError as exc:
                    raise AttachmentRejected("attachment_source_unavailable") from exc
                if stat.S_ISLNK(before.st_mode):
                    raise AttachmentRejected("symlink_attachments_are_not_allowed")
                if not stat.S_ISREG(before.st_mode):
                    raise AttachmentRejected("attachment_source_must_be_a_regular_file")
                if before.st_mode & 0o111:
                    raise AttachmentRejected("executable_attachments_are_not_allowed")
                if before.st_size > self.limits.max_file_bytes:
                    raise AttachmentLimitError("attachment_exceeds_file_limit")
                flags = os.O_RDONLY
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                try:
                    descriptor = os.open(path, flags)
                except OSError as exc:
                    raise AttachmentRejected("attachment_source_unavailable") from exc
                after = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(after.st_mode)
                    or before.st_dev != after.st_dev
                    or before.st_ino != after.st_ino
                ):
                    os.close(descriptor)
                    raise AttachmentRejected("attachment_source_changed")
                stream = stack.enter_context(os.fdopen(descriptor, "rb"))
                declared = (
                    declared_mime_by_name.get(path.name)
                    if declared_mime_by_name is not None
                    else None
                )
                incoming.append(
                    IncomingAttachment(
                        filename=path.name,
                        stream=stream,
                        declared_mime=declared,
                    )
                )
            return self.stage(incoming)

    def combine(
        self, set_ids: Iterable[str], *, discard_sources: bool = False
    ) -> AttachmentSet:
        """Revalidate staged sets into one immutable set for sequential uploads."""

        selected_ids = self._bounded_items(set_ids)
        if any(not isinstance(set_id, str) for set_id in selected_ids):
            raise AttachmentStorageError("invalid attachment set id")
        if len(set(selected_ids)) != len(selected_ids):
            raise AttachmentRejected("duplicate_attachment_sets_are_not_allowed")
        source_sets = [self.load(set_id) for set_id in selected_ids]
        source_attachments: list[tuple[AttachmentSet, dict[str, Any]]] = []
        for attachment_set in source_sets:
            for attachment in attachment_set.manifest["attachments"]:
                if not isinstance(attachment, dict):
                    raise AttachmentStorageError("invalid attachment manifest")
                source_attachments.append((attachment_set, attachment))
                if len(source_attachments) > self.limits.max_files:
                    raise AttachmentLimitError("attachment_count_exceeds_limit")

        with contextlib.ExitStack() as stack:
            incoming: list[IncomingAttachment] = []
            for attachment_set, attachment in source_attachments:
                path = self.resolve_descriptor_path(attachment_set, attachment)
                flags = os.O_RDONLY
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                try:
                    descriptor = os.open(path, flags)
                except OSError as exc:
                    raise AttachmentStorageError("attachment source unavailable") from exc
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    os.close(descriptor)
                    raise AttachmentStorageError("attachment source is not regular")
                stream = stack.enter_context(os.fdopen(descriptor, "rb"))
                incoming.append(
                    IncomingAttachment(
                        filename=str(attachment["safe_name"]),
                        stream=stream,
                        declared_mime=str(attachment["media_type"]),
                    )
                )
            combined = self.stage(incoming)

        if discard_sources:
            for attachment_set in source_sets:
                self._remove_entry(attachment_set.root)
            _fsync_directory(self.staging_root)
        return combined

    def bind_to_run(self, set_id: str, run_inputs_dir: Path) -> AttachmentSet:
        source = self._session_path(set_id)
        destination = Path(run_inputs_dir).absolute()
        if destination.name != "inputs":
            raise AttachmentStorageError("run attachment destination must be named inputs")
        parent = destination.parent
        try:
            parent_metadata = parent.lstat()
        except OSError as exc:
            raise AttachmentStorageError("run directory is unavailable") from exc
        if stat.S_ISLNK(parent_metadata.st_mode) or not stat.S_ISDIR(
            parent_metadata.st_mode
        ):
            raise AttachmentStorageError("run directory is not a safe directory")
        if os.path.lexists(destination):
            raise AttachmentStorageError("run inputs already exist")
        manifest = _read_private_json(source / "manifest.json")
        self._verify_manifest(source, set_id, manifest)
        try:
            if source.stat().st_dev != parent_metadata.st_dev:
                raise AttachmentStorageError(
                    "staging and run inputs must share a filesystem for atomic binding"
                )
            os.rename(source, destination)
            _fsync_directory(parent)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                raise AttachmentStorageError(
                    "staging and run inputs must share a filesystem for atomic binding"
                ) from exc
            raise AttachmentStorageError("atomic attachment binding failed") from exc
        return AttachmentSet(set_id=set_id, root=destination, manifest=manifest)

    def restore_from_run(self, set_id: str, run_inputs_dir: Path) -> AttachmentSet:
        """Atomically return a just-bound input set when queue intake rolls back."""

        if not isinstance(set_id, str) or _SET_ID.fullmatch(set_id) is None:
            raise AttachmentStorageError("invalid attachment set id")
        source = Path(run_inputs_dir).absolute()
        if source.name != "inputs":
            raise AttachmentStorageError("run attachment source must be named inputs")
        try:
            metadata = source.lstat()
        except OSError as exc:
            raise AttachmentStorageError("bound run inputs are unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise AttachmentStorageError("bound run inputs are not a safe directory")
        manifest = _read_private_json(source / "manifest.json")
        self._verify_manifest(source, set_id, manifest)
        destination = self.staging_root / set_id
        if os.path.lexists(destination):
            raise AttachmentStorageError("attachment staging destination already exists")
        try:
            if source.stat().st_dev != self.staging_root.stat().st_dev:
                raise AttachmentStorageError(
                    "staging and run inputs must share a filesystem for atomic rollback"
                )
            os.rename(source, destination)
            _fsync_directory(source.parent)
            _fsync_directory(self.staging_root)
        except OSError as exc:
            if exc.errno == errno.EXDEV:
                raise AttachmentStorageError(
                    "staging and run inputs must share a filesystem for atomic rollback"
                ) from exc
            raise AttachmentStorageError("atomic attachment rollback failed") from exc
        return AttachmentSet(set_id=set_id, root=destination, manifest=manifest)

    def discard_staged(self, set_ids: Iterable[str]) -> tuple[str, ...]:
        """Remove only fully revalidated staged sets after a run owns their copy."""

        selected_ids = list(set_ids)
        if not selected_ids:
            return ()
        if len(set(selected_ids)) != len(selected_ids):
            raise AttachmentStorageError("duplicate attachment set id")
        staged = [self.load(set_id) for set_id in selected_ids]
        for attachment_set in staged:
            self._remove_entry(attachment_set.root)
        _fsync_directory(self.staging_root)
        return tuple(attachment_set.set_id for attachment_set in staged)

    def discard_bound_run(self, set_id: str, run_inputs_dir: Path) -> None:
        """Delete a revalidated combined run copy when original staged sets survive."""

        if not isinstance(set_id, str) or _SET_ID.fullmatch(set_id) is None:
            raise AttachmentStorageError("invalid attachment set id")
        source = Path(run_inputs_dir).absolute()
        if source.name != "inputs":
            raise AttachmentStorageError("run attachment source must be named inputs")
        try:
            metadata = source.lstat()
        except OSError as exc:
            raise AttachmentStorageError("bound run inputs are unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise AttachmentStorageError("bound run inputs are not a safe directory")
        manifest = _read_private_json(source / "manifest.json")
        self._verify_manifest(source, set_id, manifest)
        self._remove_entry(source)
        _fsync_directory(source.parent)

    def load(self, set_id: str) -> AttachmentSet:
        root = self._session_path(set_id)
        manifest = _read_private_json(root / "manifest.json")
        self._verify_manifest(root, set_id, manifest)
        return AttachmentSet(set_id=set_id, root=root, manifest=manifest)

    def validate_set_limits(self, attachment_set: AttachmentSet) -> None:
        """Reapply this consumer's effective limits to a verified staged set."""

        attachments = attachment_set.manifest.get("attachments", [])
        if not isinstance(attachments, list):
            raise AttachmentStorageError("invalid attachment manifest")
        if not 1 <= len(attachments) <= self.limits.max_files:
            raise AttachmentLimitError("attachment_count_exceeds_limit")
        total_bytes = 0
        for attachment in attachments:
            if not isinstance(attachment, dict):
                raise AttachmentStorageError("invalid attachment manifest")
            size = attachment.get("size")
            if isinstance(size, bool) or not isinstance(size, int) or size < 1:
                raise AttachmentStorageError("invalid attachment file descriptor")
            if size > self.limits.max_file_bytes:
                raise AttachmentLimitError("attachment_exceeds_file_limit")
            total_bytes += size
        if total_bytes > self.limits.max_task_bytes:
            raise AttachmentLimitError("attachments_exceed_task_limit")
        if attachment_set.manifest.get("total_bytes") != total_bytes:
            raise AttachmentStorageError("attachment manifest total mismatch")

    def validate_runtime_ready(self, attachment_set: AttachmentSet) -> int:
        """Fail closed unless every staged file has complete runtime context."""

        self.validate_set_limits(attachment_set)
        if attachment_set.manifest.get("status") != "complete":
            raise AttachmentRejected("attachment_processing_incomplete")
        local_image_references = 0
        for attachment in attachment_set.manifest["attachments"]:
            content = attachment.get("content")
            if not isinstance(content, list) or not content:
                raise AttachmentRejected("attachment_context_is_empty")
            local_image_references += sum(
                1
                for value in content
                if isinstance(value, dict) and value.get("kind") == "local_image"
            )
            if attachment.get("kind") != "pdf":
                continue
            pdf = attachment.get("pdf")
            if (
                not isinstance(pdf, dict)
                or pdf.get("status") != "complete"
                or pdf.get("issues") != []
            ):
                raise AttachmentRejected("pdf_processing_incomplete")
        return local_image_references

    def resolve_descriptor_path(
        self, attachment_set: AttachmentSet, descriptor: Mapping[str, Any]
    ) -> Path:
        """Resolve and revalidate one manifest descriptor for a downstream consumer."""

        relative = descriptor.get("path")
        size = descriptor.get("size")
        sha256 = descriptor.get("sha256")
        if (
            not isinstance(relative, str)
            or not isinstance(size, int)
            or size < 0
            or not isinstance(sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
        ):
            raise AttachmentStorageError("invalid attachment file descriptor")
        path = self._safe_manifest_path(attachment_set.root, relative)
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != size:
            raise AttachmentStorageError("attachment file metadata mismatch")
        if _sha256(path) != sha256:
            raise AttachmentStorageError("attachment file digest mismatch")
        if descriptor.get("kind") == "local_image" and size > 10 * MIB:
            raise AttachmentStorageError("image exceeds runtime payload limit")
        return path

    def cleanup_expired(self, *, now: float | None = None) -> tuple[str, ...]:
        cutoff = (dt.datetime.now(dt.timezone.utc).timestamp() if now is None else now) - (
            self.limits.ttl_seconds
        )
        removed: list[str] = []
        with os.scandir(self.staging_root) as entries:
            for entry in entries:
                pending_match = _PENDING_ID.fullmatch(entry.name)
                if not _SET_ID.fullmatch(entry.name) and pending_match is None:
                    continue
                path = self.staging_root / entry.name
                try:
                    metadata = path.lstat()
                except FileNotFoundError:
                    continue
                created = metadata.st_mtime
                if _SET_ID.fullmatch(entry.name) and stat.S_ISDIR(metadata.st_mode):
                    try:
                        manifest = _read_private_json(path / "manifest.json")
                        created = float(manifest.get("created_at_epoch", created))
                    except (AttachmentStorageError, TypeError, ValueError):
                        pass
                if created > cutoff:
                    continue
                self._remove_entry(path)
                removed.append(pending_match.group(1) if pending_match else entry.name)
        if removed:
            _fsync_directory(self.staging_root)
        return tuple(removed)

    def _ensure_private_root(self) -> None:
        if os.path.lexists(self.staging_root):
            metadata = self.staging_root.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise AttachmentStorageError("staging root must be a real directory")
        else:
            self.staging_root.mkdir(mode=0o700, parents=True)
        os.chmod(self.staging_root, 0o700, follow_symlinks=False)
        metadata = self.staging_root.lstat()
        if metadata.st_mode & 0o077:
            raise AttachmentStorageError("staging root is not private")

    def _bounded_items(self, values: Iterable[Any]) -> list[Any]:
        items: list[Any] = []
        iterator = iter(values)
        for _ in range(self.limits.max_files + 1):
            try:
                items.append(next(iterator))
            except StopIteration:
                break
        if not items:
            raise AttachmentRejected("at_least_one_attachment_is_required")
        if len(items) > self.limits.max_files:
            raise AttachmentLimitError("attachment_count_exceeds_limit")
        return items

    def _copy_stream(
        self,
        stream: BinaryIO,
        destination: Path,
        *,
        task_bytes_before: int,
    ) -> tuple[int, str]:
        temporary = destination.with_name(f".{destination.name}.part")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as output:
                while True:
                    chunk = stream.read(self.limits.chunk_bytes)
                    if chunk is None or chunk == b"":
                        break
                    if not isinstance(chunk, bytes):
                        raise AttachmentRejected("attachment_stream_must_yield_bytes")
                    size += len(chunk)
                    if size > self.limits.max_file_bytes:
                        raise AttachmentLimitError("attachment_exceeds_file_limit")
                    if task_bytes_before + size > self.limits.max_task_bytes:
                        raise AttachmentLimitError("attachment_set_exceeds_task_limit")
                    output.write(chunk)
                    digest.update(chunk)
                if size < 1:
                    raise AttachmentRejected("empty_attachments_are_not_allowed")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
            _fsync_directory(destination.parent)
            return size, digest.hexdigest()
        finally:
            os.close(descriptor)
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _source_content_descriptor(
        attachment: dict[str, Any], kind: str
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "kind": kind,
            "path": attachment["path"],
            "media_type": attachment["media_type"],
            "size": attachment["size"],
            "sha256": attachment["sha256"],
            "provenance": {
                "attachment_id": attachment["id"],
                "source_sha256": attachment["sha256"],
            },
        }
        if "width" in attachment and "height" in attachment:
            value["width"] = attachment["width"]
            value["height"] = attachment["height"]
        return value

    def _session_path(self, set_id: str) -> Path:
        if not isinstance(set_id, str) or _SET_ID.fullmatch(set_id) is None:
            raise AttachmentStorageError("invalid attachment set id")
        path = self.staging_root / set_id
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise AttachmentStorageError("attachment set is unavailable") from exc
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise AttachmentStorageError("attachment set is not a safe directory")
        return path

    def _verify_manifest(
        self, root: Path, set_id: str, manifest: dict[str, Any]
    ) -> None:
        if manifest.get("version") != 1 or manifest.get("set_id") != set_id:
            raise AttachmentStorageError("attachment manifest identity mismatch")
        attachments = manifest.get("attachments")
        if not isinstance(attachments, list) or not 1 <= len(attachments) <= self.limits.max_files:
            raise AttachmentStorageError("invalid attachment manifest")
        expected_files: dict[str, tuple[int, str]] = {}
        for value in self._walk_values(attachments):
            if not isinstance(value, dict):
                continue
            if not {"path", "size", "sha256"}.issubset(value):
                continue
            path = value["path"]
            size = value["size"]
            sha256 = value["sha256"]
            if (
                not isinstance(path, str)
                or not isinstance(size, int)
                or size < 0
                or not isinstance(sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", sha256) is None
            ):
                raise AttachmentStorageError("invalid attachment file descriptor")
            prior = expected_files.get(path)
            if prior is not None and prior != (size, sha256):
                raise AttachmentStorageError("conflicting attachment descriptors")
            expected_files[path] = (size, sha256)
        if not expected_files:
            raise AttachmentStorageError("attachment manifest contains no files")
        for relative, (size, sha256) in expected_files.items():
            file_path = self._safe_manifest_path(root, relative)
            metadata = file_path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != size:
                raise AttachmentStorageError("attachment file metadata mismatch")
            if _sha256(file_path) != sha256:
                raise AttachmentStorageError("attachment file digest mismatch")

    @staticmethod
    def _walk_values(value: Any) -> Iterable[Any]:
        yield value
        if isinstance(value, dict):
            for nested in value.values():
                yield from AttachmentStore._walk_values(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from AttachmentStore._walk_values(nested)

    @staticmethod
    def _safe_manifest_path(root: Path, relative: str) -> Path:
        candidate = PurePosixPath(relative)
        if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
            raise AttachmentStorageError("unsafe attachment manifest path")
        current = root
        for index, part in enumerate(candidate.parts):
            if part in {"", "."}:
                raise AttachmentStorageError("unsafe attachment manifest path")
            current = current / part
            try:
                metadata = current.lstat()
            except OSError as exc:
                raise AttachmentStorageError("attachment manifest file is missing") from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise AttachmentStorageError("symlink in attachment manifest path")
            if index < len(candidate.parts) - 1 and not stat.S_ISDIR(metadata.st_mode):
                raise AttachmentStorageError("invalid attachment manifest path")
        return current

    @staticmethod
    def _remove_entry(path: Path) -> None:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)

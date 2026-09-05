from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO


MIB = 1024 * 1024
GIB = 1024 * MIB
MAX_ATTACHMENTS = 5
ABSOLUTE_MAX_FILE_BYTES = 512 * MIB
MAX_RUNTIME_IMAGE_REFERENCES = 20
MAX_PENDING_ATTACHMENT_SETS = 32
MAX_PENDING_ATTACHMENT_BYTES = 6 * GIB


class AttachmentError(RuntimeError):
    """Base class for attachment failures safe to expose as error categories."""


class AttachmentRejected(AttachmentError):
    """Raised when an untrusted attachment fails validation."""


class AttachmentLimitError(AttachmentRejected):
    """Raised when a configured attachment limit is exceeded."""


class AttachmentQuotaError(AttachmentLimitError):
    """Raised when the bounded private pending-upload pool is full."""


class AttachmentStorageError(AttachmentError):
    """Raised when private staging or atomic binding cannot be guaranteed."""


class PDFRejected(AttachmentRejected):
    """Raised when a PDF is encrypted, malformed, or cannot be processed safely."""


@dataclass(frozen=True, slots=True)
class AttachmentLimits:
    """Hard and configurable bounds for one attachment set."""

    max_files: int = MAX_ATTACHMENTS
    max_file_bytes: int = 100 * MIB
    max_task_bytes: int = 500 * MIB
    chunk_bytes: int = MIB
    ttl_seconds: int = 24 * 60 * 60
    max_image_pixels: int = 50_000_000
    max_runtime_image_bytes: int = 10 * MIB

    def __post_init__(self) -> None:
        if not 1 <= self.max_files <= MAX_ATTACHMENTS:
            raise ValueError(f"max_files must be between 1 and {MAX_ATTACHMENTS}")
        if not 1 <= self.max_file_bytes <= ABSOLUTE_MAX_FILE_BYTES:
            raise ValueError(
                f"max_file_bytes must be between 1 and {ABSOLUTE_MAX_FILE_BYTES}"
            )
        if not 1 <= self.max_task_bytes <= self.max_files * ABSOLUTE_MAX_FILE_BYTES:
            raise ValueError("max_task_bytes exceeds the hard attachment-set bound")
        if not 4096 <= self.chunk_bytes <= 8 * MIB:
            raise ValueError("chunk_bytes must be between 4096 bytes and 8 MiB")
        if self.ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")
        if self.max_image_pixels < 1:
            raise ValueError("max_image_pixels must be positive")
        if not 1 <= self.max_runtime_image_bytes <= 10 * MIB:
            raise ValueError("max_runtime_image_bytes must be between 1 byte and 10 MiB")


@dataclass(frozen=True, slots=True)
class IncomingAttachment:
    """An untrusted upload stream and its client-supplied metadata."""

    filename: str
    stream: BinaryIO
    declared_mime: str | None = None


@dataclass(frozen=True, slots=True)
class AttachmentSet:
    """A staged or bound attachment set backed by a JSON manifest."""

    set_id: str
    root: Path
    manifest: dict[str, Any]

    @property
    def manifest_path(self) -> Path:
        return self.root / "manifest.json"

    @property
    def status(self) -> str:
        return str(self.manifest.get("status", "unknown"))

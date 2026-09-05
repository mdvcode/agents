"""Provider-neutral, file-backed attachment ingestion."""

from .models import (
    ABSOLUTE_MAX_FILE_BYTES,
    MAX_ATTACHMENTS,
    MAX_PENDING_ATTACHMENT_BYTES,
    MAX_PENDING_ATTACHMENT_SETS,
    MAX_RUNTIME_IMAGE_REFERENCES,
    AttachmentError,
    AttachmentLimitError,
    AttachmentLimits,
    AttachmentQuotaError,
    AttachmentRejected,
    AttachmentSet,
    AttachmentStorageError,
    IncomingAttachment,
    PDFRejected,
)
from .pdf import PDFLimits, PDFProcessor, PDFToolchain
from .runtime import (
    AttachmentContextError,
    attachment_image_paths,
    attachment_text_context,
    compile_attachment_context,
)
from .store import AttachmentStore

__all__ = [
    "ABSOLUTE_MAX_FILE_BYTES",
    "MAX_ATTACHMENTS",
    "MAX_PENDING_ATTACHMENT_BYTES",
    "MAX_PENDING_ATTACHMENT_SETS",
    "MAX_RUNTIME_IMAGE_REFERENCES",
    "AttachmentError",
    "AttachmentContextError",
    "AttachmentLimitError",
    "AttachmentLimits",
    "AttachmentQuotaError",
    "AttachmentRejected",
    "AttachmentSet",
    "AttachmentStorageError",
    "AttachmentStore",
    "IncomingAttachment",
    "PDFLimits",
    "PDFProcessor",
    "PDFRejected",
    "PDFToolchain",
    "attachment_image_paths",
    "attachment_text_context",
    "compile_attachment_context",
]

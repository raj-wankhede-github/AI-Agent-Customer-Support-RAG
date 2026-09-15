"""Upload validation and basic file-safety checks.

Validation is based on content, not on the client-declared filename or MIME type alone.
The heuristic scanner rejects active content (PDF JavaScript/launch actions/embedded
files, macro-enabled Office files, zip bombs). It is not a replacement for an antivirus
engine; `MalwareScanner` is the seam for plugging one in (e.g. ClamAV).
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol

from app.core.errors import (
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
    ValidationAppError,
)
from app.models.enums import SourceType

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

_TYPES: dict[str, tuple[SourceType, str, frozenset[str]]] = {
    ".pdf": (SourceType.PDF, "application/pdf", frozenset({"application/pdf"})),
    ".txt": (SourceType.TXT, "text/plain", frozenset({"text/plain"})),
    ".md": (SourceType.MARKDOWN, "text/markdown", frozenset({"text/markdown", "text/x-markdown", "text/plain"})),
    ".markdown": (SourceType.MARKDOWN, "text/markdown", frozenset({"text/markdown", "text/x-markdown", "text/plain"})),
    ".html": (SourceType.HTML, "text/html", frozenset({"text/html"})),
    ".htm": (SourceType.HTML, "text/html", frozenset({"text/html"})),
    ".docx": (SourceType.DOCX, DOCX_MIME, frozenset({DOCX_MIME})),
}  # fmt: skip
_GENERIC_MIME = frozenset({"", "application/octet-stream", "binary/octet-stream"})
_PDF_ACTIVE_CONTENT = re.compile(rb"/(JavaScript|JS|Launch|EmbeddedFile|RichMedia|XFA)\b")
_MAX_DOCX_UNCOMPRESSED = 200 * 1024 * 1024
_MAX_DOCX_RATIO = 100

ALLOWED_EXTENSIONS = tuple(sorted(_TYPES))


@dataclass(frozen=True)
class ValidatedFile:
    source_type: SourceType
    mime_type: str
    extension: str


class MalwareScanner(Protocol):
    def scan(self, source_type: SourceType, data: bytes) -> str | None:
        """Return a reason string when the file must be rejected."""


class HeuristicScanner:
    def scan(self, source_type: SourceType, data: bytes) -> str | None:
        if source_type is SourceType.PDF:
            match = _PDF_ACTIVE_CONTENT.search(data)
            if match:
                return f"PDF contains active content ({match.group(1).decode()})"
        if source_type is SourceType.DOCX:
            return _scan_docx(data)
        return None


def _scan_docx(data: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if "word/document.xml" not in names or "[Content_Types].xml" not in names:
                return "File is not a valid DOCX document"
            total = 0
            for info in archive.infolist():
                path = PurePosixPath(info.filename)
                if path.is_absolute() or ".." in path.parts:
                    return "Archive contains unsafe paths"
                if info.filename.lower().endswith("vbaproject.bin"):
                    return "Macro-enabled documents are not accepted"
                total += info.file_size
            if total > _MAX_DOCX_UNCOMPRESSED or total > len(data) * _MAX_DOCX_RATIO:
                return "Archive expands to an unsafe size"
            content_types = archive.read("[Content_Types].xml")
            if b"macroEnabled" in content_types:
                return "Macro-enabled documents are not accepted"
    except zipfile.BadZipFile:
        return "File is not a valid DOCX document"
    return None


def validate_upload(
    filename: str,
    data: bytes,
    declared_content_type: str | None,
    max_size: int,
    scanner: MalwareScanner | None = None,
) -> ValidatedFile:
    if not data:
        raise ValidationAppError("The uploaded file is empty.")
    if len(data) > max_size:
        raise PayloadTooLargeError(f"The file exceeds the maximum upload size of {max_size // (1024 * 1024)} MB.")
    extension = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if extension not in _TYPES:
        raise UnsupportedMediaTypeError(f"Unsupported file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}.")
    source_type, mime_type, accepted = _TYPES[extension]
    declared = (declared_content_type or "").split(";")[0].strip().lower()
    if declared not in _GENERIC_MIME and declared not in accepted:
        raise UnsupportedMediaTypeError("The file content type does not match its extension.")

    if source_type is SourceType.PDF:
        if not data[:1024].lstrip().startswith(b"%PDF-"):
            raise UnsupportedMediaTypeError("The file is not a valid PDF.")
    elif source_type is SourceType.DOCX:
        if not data.startswith(b"PK"):
            raise UnsupportedMediaTypeError("The file is not a valid DOCX document.")
    else:
        if b"\x00" in data[:8192]:
            raise UnsupportedMediaTypeError("Text documents must not contain binary data.")
        try:
            data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValidationAppError("Text documents must be UTF-8 encoded.") from exc

    reason = (scanner or HeuristicScanner()).scan(source_type, data)
    if reason:
        raise ValidationAppError(f"The file was rejected by the safety check: {reason}.")
    return ValidatedFile(source_type=source_type, mime_type=mime_type, extension=extension)

"""PDF loader (text layer only; scanned PDFs without a text layer fail with NO_TEXT)."""

from __future__ import annotations

import io

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.core.errors import IngestionError
from app.ingestion.loaders.base import lines_to_blocks, pick_title
from app.ingestion.normalize import normalize_text
from app.ingestion.types import Block, ExtractedDocument
from app.models.enums import SourceType


class PdfLoader:
    source_type = SourceType.PDF

    def __init__(self, max_pages: int) -> None:
        self.max_pages = max_pages

    def load(self, data: bytes, filename: str) -> ExtractedDocument:
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted and not reader.decrypt(""):
                raise IngestionError("Password-protected PDFs are not supported.", error_code="ENCRYPTED_PDF")
            page_count = len(reader.pages)
            if page_count > self.max_pages:
                raise IngestionError(
                    f"The PDF has {page_count} pages; the limit is {self.max_pages}.",
                    error_code="TOO_MANY_PAGES",
                )
            blocks: list[Block] = []
            for number, page in enumerate(reader.pages, start=1):
                text = normalize_text(page.extract_text() or "")
                blocks.extend(lines_to_blocks(text, page=number))
            meta_title = (reader.metadata.title if reader.metadata else None) or None
        except IngestionError:
            raise
        except (PdfReadError, ValueError, KeyError, TypeError) as exc:
            raise IngestionError("The PDF could not be read. It may be corrupted.", error_code="CORRUPT_FILE") from exc
        return ExtractedDocument(
            blocks=blocks,
            title=str(meta_title) if meta_title else pick_title(blocks, None),
            page_count=page_count,
        )

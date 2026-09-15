from __future__ import annotations

from app.core.config import Settings
from app.ingestion.loaders.base import DocumentLoader
from app.ingestion.loaders.docx import DocxLoader
from app.ingestion.loaders.html import HtmlLoader
from app.ingestion.loaders.pdf import PdfLoader
from app.ingestion.loaders.text import MarkdownLoader, TextLoader
from app.models.enums import SourceType


def get_loader(source_type: SourceType, settings: Settings) -> DocumentLoader:
    loaders: dict[SourceType, DocumentLoader] = {
        SourceType.PDF: PdfLoader(max_pages=settings.max_document_pages),
        SourceType.DOCX: DocxLoader(),
        SourceType.HTML: HtmlLoader(),
        SourceType.MARKDOWN: MarkdownLoader(),
        SourceType.TXT: TextLoader(),
    }
    return loaders[source_type]

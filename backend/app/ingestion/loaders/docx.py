"""DOCX loader preserving heading styles, list paragraphs and tables in document order."""

from __future__ import annotations

import io
import re
import zipfile

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.core.errors import IngestionError
from app.ingestion.loaders.base import pick_title
from app.ingestion.normalize import normalize_text
from app.ingestion.types import Block, ExtractedDocument
from app.models.enums import SourceType

_HEADING_STYLE = re.compile(r"^heading\s*(\d)$", re.IGNORECASE)


class DocxLoader:
    source_type = SourceType.DOCX

    def load(self, data: bytes, filename: str) -> ExtractedDocument:
        try:
            document = docx.Document(io.BytesIO(data))
        except (zipfile.BadZipFile, KeyError, ValueError) as exc:
            raise IngestionError(
                "The DOCX file could not be read. It may be corrupted.", error_code="CORRUPT_FILE"
            ) from exc

        blocks: list[Block] = []
        list_items: list[str] = []

        def flush_list() -> None:
            if list_items:
                blocks.append(Block("list", "\n".join(f"- {i}" for i in list_items)))
                list_items.clear()

        for element in document.element.body.iterchildren():
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "p":
                paragraph = Paragraph(element, document)
                text = normalize_text(paragraph.text)
                if not text:
                    continue
                style = (paragraph.style.name if paragraph.style is not None else "") or ""
                heading = _HEADING_STYLE.match(style)
                is_list = "list" in style.lower() or (element.pPr is not None and element.pPr.numPr is not None)
                if style.lower() == "title" or heading:
                    flush_list()
                    blocks.append(Block("heading", text, level=int(heading.group(1)) if heading else 1))
                elif is_list:
                    list_items.append(text)
                else:
                    flush_list()
                    blocks.append(Block("paragraph", text))
            elif tag == "tbl":
                flush_list()
                rows = []
                for row in Table(element, document).rows:
                    cells = [normalize_text(cell.text) for cell in row.cells]
                    if any(cells):
                        rows.append(" | ".join(cells))
                if rows:
                    blocks.append(Block("table", "\n".join(rows)))
        flush_list()
        core_title = document.core_properties.title or None
        return ExtractedDocument(blocks=blocks, title=pick_title(blocks, core_title) or core_title)

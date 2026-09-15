"""Loader interface and shared plain-text structure heuristics.

Adding a format means implementing `DocumentLoader` and registering it in
`app.ingestion.loaders.registry`.
"""

from __future__ import annotations

import re
from typing import Protocol

from app.ingestion.types import Block, ExtractedDocument
from app.models.enums import SourceType

_LIST_ITEM = re.compile(r"^\s*(?:[-*+•●▪]|\(?\d{1,2}[.)]|[a-z][.)])\s+\S")
_NUMBERED_HEADING = re.compile(r"^(?:\d+(?:\.\d+)*\.?|[IVX]+\.)\s+[A-Z]")


class DocumentLoader(Protocol):
    source_type: SourceType

    def load(self, data: bytes, filename: str) -> ExtractedDocument: ...


def looks_like_heading(line: str, next_line: str | None) -> bool:
    stripped = line.strip()
    if not stripped or len(stripped) > 80 or next_line is None:
        return False
    if stripped[-1] in ".,;!?" or _LIST_ITEM.match(stripped):
        return False
    words = stripped.split()
    if len(words) > 10:
        return False
    letters = [c for c in stripped if c.isalpha()]
    if letters and all(c.isupper() for c in letters) and len(letters) >= 3:
        return True
    if _NUMBERED_HEADING.match(stripped):
        return True
    if stripped.endswith(":") and len(words) <= 6:
        return True
    capitalized = sum(1 for w in words if w[0].isupper() or not w[0].isalpha())
    return len(words) >= 2 and capitalized / len(words) >= 0.8


def lines_to_blocks(text: str, page: int | None = None) -> list[Block]:
    """Group raw lines (PDF page text, plain text) into headings, paragraphs and lists."""
    lines = text.splitlines()
    blocks: list[Block] = []
    paragraph: list[str] = []
    items: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(Block("paragraph", " ".join(paragraph), page=page))
            paragraph.clear()
        if items:
            blocks.append(Block("list", "\n".join(items), page=page))
            items.clear()

    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line:
            flush()
            continue
        next_line = next((ln for ln in lines[i + 1 :] if ln.strip()), None)
        if _LIST_ITEM.match(line):
            if paragraph:
                blocks.append(Block("paragraph", " ".join(paragraph), page=page))
                paragraph.clear()
            items.append(line)
        elif (not paragraph or paragraph[-1].endswith((".", "!", "?", ":"))) and looks_like_heading(line, next_line):
            # PDFs rarely keep blank lines, so a heading may directly follow a finished sentence.
            flush()
            level = 1 if line.isupper() or not blocks else 2
            blocks.append(Block("heading", line.rstrip(":"), level=level, page=page))
        elif items and not paragraph:
            items[-1] = f"{items[-1]} {line}"  # wrapped list item
        else:
            paragraph.append(line)
    flush()
    return blocks


def pick_title(blocks: list[Block], fallback: str | None) -> str | None:
    for block in blocks:
        if block.kind == "heading" and block.level == 1:
            return block.text[:300]
    return fallback

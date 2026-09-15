"""Plain-text and Markdown loaders."""

from __future__ import annotations

import re

from app.ingestion.loaders.base import lines_to_blocks, pick_title
from app.ingestion.normalize import normalize_text
from app.ingestion.types import Block, ExtractedDocument
from app.models.enums import SourceType

_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_MD_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_TABLE_DIVIDER = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
_FRONT_MATTER = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_INLINE_MD = re.compile(r"(\*\*|__|`)(.+?)\1")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


class TextLoader:
    source_type = SourceType.TXT

    def load(self, data: bytes, filename: str) -> ExtractedDocument:
        text = normalize_text(data.decode("utf-8-sig"))
        blocks = lines_to_blocks(text)
        return ExtractedDocument(blocks=blocks, title=pick_title(blocks, None))


def _clean_inline(text: str) -> str:
    text = _LINK.sub(r"\1 (\2)", text)
    return _INLINE_MD.sub(r"\2", text)


class MarkdownLoader:
    source_type = SourceType.MARKDOWN

    def load(self, data: bytes, filename: str) -> ExtractedDocument:
        text = data.decode("utf-8-sig").replace("\r\n", "\n")
        metadata: dict[str, str] = {}
        front = _FRONT_MATTER.match(text)
        if front:
            for line in front.group(1).splitlines():
                key, sep, value = line.partition(":")
                if sep:
                    metadata[key.strip().lower()] = value.strip().strip("\"'")
            text = text[front.end() :]
        text = normalize_text(text)

        blocks: list[Block] = []
        buffer: list[str] = []
        kind: str | None = None
        in_code = False

        def flush() -> None:
            nonlocal kind
            if buffer and kind:
                joined = "\n".join(buffer) if kind in ("list", "table") else " ".join(buffer)
                blocks.append(Block(kind, _clean_inline(joined)))  # type: ignore[arg-type]
            buffer.clear()
            kind = None

        for line in text.splitlines():
            if line.strip().startswith("```"):
                flush()
                in_code = not in_code
                continue
            if in_code:
                buffer.append(line)
                kind = "paragraph"
                continue
            heading = _ATX_HEADING.match(line)
            if heading:
                flush()
                blocks.append(Block("heading", _clean_inline(heading.group(2)), level=len(heading.group(1))))
            elif not line.strip():
                flush()
            elif _TABLE_ROW.match(line):
                if kind != "table":
                    flush()
                    kind = "table"
                if not _TABLE_DIVIDER.match(line):
                    cells = [c.strip() for c in line.strip().strip("|").split("|")]
                    buffer.append(" | ".join(cells))
            elif _MD_LIST.match(line):
                if kind != "list":
                    flush()
                    kind = "list"
                buffer.append(line.strip())
            elif kind == "list" and line.startswith((" ", "\t")):
                buffer[-1] = f"{buffer[-1]} {line.strip()}"
            else:
                if kind not in (None, "paragraph"):
                    flush()
                kind = "paragraph"
                buffer.append(line.strip())
        flush()
        title = metadata.get("title") or pick_title(blocks, None)
        return ExtractedDocument(blocks=blocks, title=title, metadata=metadata)

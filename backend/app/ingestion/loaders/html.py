"""HTML loader built on the standard-library parser (no network fetches, no scripts)."""

from __future__ import annotations

from html.parser import HTMLParser

from app.ingestion.loaders.base import pick_title
from app.ingestion.normalize import normalize_text
from app.ingestion.types import Block, BlockKind, ExtractedDocument
from app.models.enums import SourceType

_SKIP = {"script", "style", "noscript", "template", "svg", "nav", "footer", "header", "aside", "form", "iframe"}
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_BLOCK = {"p", "div", "section", "article", "main", "blockquote", "pre", "dd", "dt", "br", "body"}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.title: str | None = None
        self.canonical: str | None = None
        self._skip_depth = 0
        self._in_title = False
        self._buffer: list[str] = []
        self._heading_level = 0
        self._list_items: list[str] = []
        self._rows: list[str] = []
        self._cells: list[str] = []
        self._in_li = False

    def _text(self) -> str:
        text = " ".join(" ".join(self._buffer).split())
        self._buffer.clear()
        return text

    def _emit(self, kind: BlockKind, text: str, level: int = 0) -> None:
        if text:
            self.blocks.append(Block(kind, text, level=level))

    def _flush_paragraph(self) -> None:
        self._emit("paragraph", self._text())

    def _flush_groups(self) -> None:
        if self._list_items:
            self._emit("list", "\n".join(f"- {item}" for item in self._list_items))
            self._list_items = []
        if self._rows:
            self._emit("table", "\n".join(self._rows))
            self._rows = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skip_depth or tag in _SKIP:
            if tag in _SKIP:
                self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        elif tag == "link" and dict(attrs).get("rel") == "canonical":
            self.canonical = dict(attrs).get("href")
        elif tag in _HEADINGS:
            self._flush_paragraph()
            self._flush_groups()
            self._heading_level = _HEADINGS[tag]
        elif tag == "li":
            self._flush_paragraph()
            self._in_li = True
        elif tag in ("td", "th"):
            self._text()
        elif tag == "tr":
            self._cells = []
        elif (tag in _BLOCK or tag in ("ul", "ol", "table")) and not self._in_li:
            self._flush_paragraph()

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag == "title":
            self.title = self._text() or None
            self._in_title = False
        elif tag in _HEADINGS:
            self._emit("heading", self._text(), self._heading_level)
            self._heading_level = 0
        elif tag == "li":
            text = self._text()
            if text:
                self._list_items.append(text)
            self._in_li = False
        elif tag in ("td", "th"):
            self._cells.append(self._text())
        elif tag == "tr":
            if any(self._cells):
                self._rows.append(" | ".join(self._cells))
        elif tag in ("ul", "ol", "table"):
            self._flush_groups()
        elif tag in _BLOCK and not self._in_li:
            self._flush_paragraph()

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self._buffer.append(data)

    def finish(self) -> None:
        self._flush_paragraph()
        self._flush_groups()


class HtmlLoader:
    source_type = SourceType.HTML

    def load(self, data: bytes, filename: str) -> ExtractedDocument:
        parser = _Extractor()
        parser.feed(data.decode("utf-8-sig", errors="replace"))
        parser.close()
        parser.finish()
        blocks = [Block(b.kind, normalize_text(b.text), level=b.level) for b in parser.blocks if b.text.strip()]
        metadata = {"source_uri": parser.canonical} if parser.canonical else {}
        return ExtractedDocument(blocks=blocks, title=pick_title(blocks, parser.title), metadata=metadata)

"""Structure-aware chunking.

Chunks never cross a section (heading) boundary, never split a paragraph, list or table
unless that single unit exceeds the hard token cap, and carry the heading path, page
range and block kinds so every answer can say exactly where its evidence came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.ingestion.types import Block, Chunk, ExtractedDocument
from app.utils.text import estimate_tokens, split_sentences


@dataclass
class _Unit:
    text: str
    kind: str
    page: int | None
    tokens: int = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = estimate_tokens(self.text)


@dataclass
class _Section:
    heading_path: list[str]
    units: list[_Unit] = field(default_factory=list)


class StructureAwareChunker:
    def __init__(self, target_tokens: int, max_tokens: int, overlap_tokens: int) -> None:
        if not 0 <= overlap_tokens < target_tokens <= max_tokens:
            raise ValueError("Require 0 <= overlap < target <= max chunk tokens")
        self.target = target_tokens
        self.max = max_tokens
        self.overlap = overlap_tokens

    def chunk(self, document: ExtractedDocument) -> list[Chunk]:
        chunks: list[Chunk] = []
        for section in self._sections(document.blocks):
            for group in self._pack(section.units):
                content = "\n\n".join(u.text for u in group).strip()
                if not content:
                    continue
                pages = [u.page for u in group if u.page is not None]
                chunks.append(
                    Chunk(
                        index=len(chunks),
                        content=content,
                        section_title=section.heading_path[-1] if section.heading_path else None,
                        heading_path=" > ".join(section.heading_path) or None,
                        page_number=min(pages) if pages else None,
                        page_end=max(pages) if pages else None,
                        token_count=estimate_tokens(content),
                        metadata={"block_kinds": sorted({u.kind for u in group})},
                    )
                )
        return chunks

    def _sections(self, blocks: list[Block]) -> list[_Section]:
        sections: list[_Section] = []
        stack: list[tuple[int, str]] = []
        current = _Section(heading_path=[])
        for block in blocks:
            if block.kind == "heading":
                if current.units:
                    sections.append(current)
                while stack and stack[-1][0] >= block.level:
                    stack.pop()
                stack.append((block.level, block.text))
                current = _Section(heading_path=[title for _, title in stack])
                continue
            current.units.extend(self._split_oversized(block))
        if current.units:
            sections.append(current)
        return sections

    def _split_oversized(self, block: Block) -> list[_Unit]:
        unit = _Unit(block.text, block.kind, block.page)
        if unit.tokens <= self.max:
            return [unit]
        if block.kind == "table":
            rows = block.text.splitlines()
            header, body = rows[0], rows[1:]
            return self._group_lines(body, block, prefix=header)
        if block.kind == "list":
            return self._group_lines(block.text.splitlines(), block)
        return self._group_lines(split_sentences(block.text), block, joiner=" ")

    def _group_lines(
        self, lines: list[str], block: Block, prefix: str | None = None, joiner: str = "\n"
    ) -> list[_Unit]:
        units: list[_Unit] = []
        current: list[str] = [prefix] if prefix else []
        for line in lines:
            candidate = joiner.join([*current, line])
            if current and current != [prefix] and estimate_tokens(candidate) > self.target:
                units.append(_Unit(joiner.join(current), block.kind, block.page))
                current = [prefix, line] if prefix else [line]
            else:
                current.append(line)
        if current and current != [prefix]:
            units.append(_Unit(joiner.join(current), block.kind, block.page))
        return units

    def _pack(self, units: list[_Unit]) -> list[list[_Unit]]:
        groups: list[list[_Unit]] = []
        current: list[_Unit] = []
        size = 0
        for unit in units:
            if current and size + unit.tokens > self.target:
                groups.append(current)
                overlap = self._overlap(current)
                current = [overlap] if overlap and overlap.tokens + unit.tokens <= self.max else []
                size = sum(u.tokens for u in current)
            current.append(unit)
            size += unit.tokens
        if current:
            groups.append(current)
        return groups

    def _overlap(self, group: list[_Unit]) -> _Unit | None:
        """Trailing sentences of the previous chunk, up to the overlap budget."""
        if self.overlap == 0:
            return None
        last = group[-1]
        if last.kind == "table":
            return None
        picked: list[str] = []
        for sentence in reversed(split_sentences(last.text)):
            if estimate_tokens(" ".join([sentence, *picked])) > self.overlap:
                break
            picked.insert(0, sentence)
        return _Unit(" ".join(picked), "overlap", last.page) if picked else None

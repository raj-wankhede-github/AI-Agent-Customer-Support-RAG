from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

BlockKind = Literal["heading", "paragraph", "list", "table"]


@dataclass
class Block:
    kind: BlockKind
    text: str
    level: int = 0  # heading level (1 = top)
    page: int | None = None


@dataclass
class ExtractedDocument:
    blocks: list[Block]
    title: str | None = None
    page_count: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return sum(len(b.text) for b in self.blocks)


@dataclass
class Chunk:
    index: int
    content: str
    section_title: str | None
    heading_path: str | None
    page_number: int | None
    page_end: int | None
    token_count: int
    metadata: dict[str, Any] = field(default_factory=dict)

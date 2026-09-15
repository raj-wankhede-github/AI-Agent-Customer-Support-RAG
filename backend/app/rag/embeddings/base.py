from __future__ import annotations

from typing import Protocol


class EmbeddingProvider(Protocol):
    """Embeds text into vectors. `model` is persisted with every vector so embeddings
    from incompatible models are never compared."""

    name: str
    model: str
    dimension: int

    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


def embedding_input(document_title: str, heading_path: str | None, content: str) -> str:
    """Contextual header: the chunk is embedded with its document title and section path
    so short chunks stay attributable, while the stored content remains verbatim."""
    header = " > ".join(p for p in (document_title, heading_path) if p)
    return f"{header}\n\n{content}" if header else content

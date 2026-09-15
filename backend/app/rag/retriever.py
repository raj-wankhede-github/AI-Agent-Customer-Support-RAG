"""Hybrid retrieval over PostgreSQL: pgvector cosine search + full-text search, fused
with Reciprocal Rank Fusion.

Isolation and eligibility are enforced in SQL, not in Python:
- tenant: every table is filtered by company_id;
- eligibility: only the document's active version (set only after successful ingestion,
  embedding and validation), only ACTIVE documents, only READY versions;
- model compatibility: only versions indexed with the current embedding model, and only
  vectors of that model and dimension are compared.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.core.errors import EmbeddingError, RetrievalError
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.types import RetrievalFilters, RetrievalResult, RetrievedChunk
from app.utils.text import STOPWORDS, words

log = structlog.get_logger(__name__)

# Standard RRF constant (Cormack et al.): dampens the influence of top ranks so that a
# chunk ranked well by both retrievers beats one ranked first by only one of them.
_RRF_K = 60
_HNSW_EF_SEARCH = 100  # larger candidate list than the default 40, since results are post-filtered

_ELIGIBILITY = """
    JOIN documents d ON d.id = c.document_id
    JOIN document_versions v ON v.id = c.document_version_id
    WHERE c.company_id = :company_id
      AND d.company_id = :company_id
      AND d.status = 'ACTIVE'
      AND d.active_version_id = c.document_version_id
      AND v.status = 'READY'
      AND v.embedding_model = :model
      AND (CAST(:product AS text) IS NULL OR d.product = CAST(:product AS text))
      AND (CAST(:category AS text) IS NULL OR d.category = CAST(:category AS text))
      AND (CAST(:locale AS text) IS NULL OR d.locale = CAST(:locale AS text))
      AND (CAST(:authorities AS text[]) IS NULL OR d.authority = ANY(CAST(:authorities AS text[])))
"""


def build_lexical_query(query: str) -> str:
    """OR-combined terms for to_tsquery; only [a-z0-9] tokens, so no tsquery syntax injection."""
    terms = []
    for word in words(query):
        token = re.sub(r"[^a-z0-9]", "", word)
        if token and token not in STOPWORDS and len(token) > 1 and token not in terms:
            terms.append(token)
    return " | ".join(terms[:20])


class PgHybridRetriever:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedder: EmbeddingProvider,
        settings: Settings,
    ) -> None:
        self._sessions = session_factory
        self._embedder = embedder
        self._settings = settings

    async def retrieve(self, *, company_id: uuid.UUID, query: str, filters: RetrievalFilters) -> RetrievalResult:
        try:
            query_vector = await self._embedder.embed_query(query)
        except EmbeddingError as exc:
            raise RetrievalError(detail=f"query embedding failed: {exc.detail}") from exc

        dim = int(self._embedder.dimension)
        vector_literal = "[" + ",".join(f"{v:.7f}" for v in query_vector) + "]"
        lexical_query = build_lexical_query(query)
        params: dict[str, Any] = {
            "company_id": company_id,
            "model": self._embedder.model,
            "product": filters.product,
            "category": filters.category,
            "locale": filters.locale,
            "authorities": filters.authorities,
            "qvec": vector_literal,
            "k": self._settings.rag_top_k,
        }
        # Only the integer dimension is interpolated (casts and partial-index predicates cannot
        # be bind parameters); every user-influenced value is a bound parameter.
        qvec_sql = f"CAST(CAST(:qvec AS text) AS vector({dim}))"
        vector_sql = f"""
            SELECT c.id AS chunk_id, 1 - (e.embedding::vector({dim}) <=> {qvec_sql}) AS similarity
            FROM chunk_embeddings e
            JOIN document_chunks c ON c.id = e.chunk_id
            {_ELIGIBILITY}
              AND e.company_id = :company_id
              AND e.embedding_model = :model
              AND e.dimension = {dim}
            ORDER BY e.embedding::vector({dim}) <=> {qvec_sql}
            LIMIT :k
        """
        lexical_sql = f"""
            SELECT c.id AS chunk_id, ts_rank_cd(c.search_vector, q, 32) AS rank
            FROM document_chunks c
            CROSS JOIN to_tsquery('english', :tsquery) AS q
            {_ELIGIBILITY}
              AND c.search_vector @@ q
            ORDER BY rank DESC
            LIMIT :k
        """
        try:
            async with self._sessions() as session, session.begin():
                await session.execute(text(f"SET LOCAL hnsw.ef_search = {_HNSW_EF_SEARCH}"))
                vector_rows = (await session.execute(text(vector_sql), params)).all()
                lexical_rows: Sequence[Any] = []
                if lexical_query:
                    lexical_rows = (
                        await session.execute(text(lexical_sql), {**params, "tsquery": lexical_query})
                    ).all()
                vector_ranks = {row.chunk_id: (i + 1, float(row.similarity)) for i, row in enumerate(vector_rows)}
                lexical_ranks = {row.chunk_id: (i + 1, float(row.rank)) for i, row in enumerate(lexical_rows)}
                eligible = [
                    cid
                    for cid, (_, sim) in vector_ranks.items()
                    if sim >= self._settings.rag_min_score or cid in lexical_ranks
                ] + [cid for cid in lexical_ranks if cid not in vector_ranks]
                details = await self._load(session, company_id, eligible, qvec_sql, params) if eligible else []
        except RetrievalError:
            raise
        except Exception as exc:
            log.error("retrieval_failed", error=type(exc).__name__)
            raise RetrievalError(detail=f"retrieval query failed: {type(exc).__name__}: {exc}") from exc

        candidates = []
        for chunk in details:
            fused = 0.0
            if chunk.chunk_id in vector_ranks:
                chunk.vector_rank = vector_ranks[chunk.chunk_id][0]
                fused += 1 / (_RRF_K + chunk.vector_rank)
            if chunk.chunk_id in lexical_ranks:
                chunk.lexical_rank, chunk.lexical_score = lexical_ranks[chunk.chunk_id]
                fused += 1 / (_RRF_K + chunk.lexical_rank)
            chunk.fused_score = fused
            candidates.append(chunk)
        candidates.sort(key=lambda c: c.fused_score, reverse=True)
        log.info(
            "retrieval_complete",
            vector_hits=len(vector_rows),
            lexical_hits=len(lexical_rows),
            candidates=len(candidates),
        )
        return RetrievalResult(candidates, self._embedder.model, lexical_query)

    async def _load(
        self,
        session: AsyncSession,
        company_id: uuid.UUID,
        chunk_ids: list[uuid.UUID],
        qvec_sql: str,
        params: dict[str, Any],
    ) -> list[RetrievedChunk]:
        dim = int(self._embedder.dimension)
        sql = f"""
            SELECT c.id, c.document_id, c.document_version_id, c.chunk_index, c.content,
                   c.content_hash, c.section_title, c.heading_path, c.page_number, c.page_end,
                   c.metadata, d.title, d.source_type, d.source_uri, d.authority, d.category,
                   d.product, d.locale, coalesce(v.effective_date, d.effective_date) AS effective_date,
                   v.version_number,
                   CASE WHEN e.id IS NULL THEN 0
                        ELSE 1 - (e.embedding::vector({dim}) <=> {qvec_sql}) END AS similarity
            FROM document_chunks c
            JOIN documents d ON d.id = c.document_id
            JOIN document_versions v ON v.id = c.document_version_id
            LEFT JOIN chunk_embeddings e
                   ON e.chunk_id = c.id AND e.embedding_model = :model AND e.dimension = {dim}
            WHERE c.id = ANY(CAST(:ids AS uuid[])) AND c.company_id = :company_id
        """
        rows = (
            await session.execute(
                text(sql),
                {"ids": chunk_ids, "company_id": company_id, "model": params["model"], "qvec": params["qvec"]},
            )
        ).all()
        return [
            RetrievedChunk(
                chunk_id=row.id,
                document_id=row.document_id,
                document_version_id=row.document_version_id,
                document_title=row.title,
                version_number=row.version_number,
                source_type=row.source_type,
                source_uri=row.source_uri,
                authority=row.authority,
                category=row.category,
                product=row.product,
                locale=row.locale,
                effective_date=row.effective_date,
                chunk_index=row.chunk_index,
                content=row.content,
                content_hash=row.content_hash,
                section_title=row.section_title,
                heading_path=row.heading_path,
                page_number=row.page_number,
                page_end=row.page_end,
                metadata=row.metadata or {},
                vector_similarity=max(0.0, float(row.similarity or 0.0)),
            )
            for row in rows
        ]

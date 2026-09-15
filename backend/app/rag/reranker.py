"""Reranking and de-duplication of retrieval candidates."""

from __future__ import annotations

import math
from typing import Protocol

import structlog
from pydantic import BaseModel

from app.core.config import Settings
from app.core.errors import LLMError
from app.llm.prompts import get_prompt
from app.llm.structured import StructuredLLM
from app.rag.types import RetrievedChunk
from app.utils.text import jaccard, term_set, truncate

log = structlog.get_logger(__name__)


class Reranker(Protocol):
    name: str

    async def rerank(
        self, query: str, key_terms: list[str], candidates: list[RetrievedChunk]
    ) -> list[RetrievedChunk]: ...


def chunk_terms(chunk: RetrievedChunk) -> set[str]:
    """Terms a chunk can vouch for: its content plus document title, section path and product."""
    return term_set(f"{chunk.document_title} {chunk.heading_path or ''} {chunk.product or ''} {chunk.content}")


class HeuristicReranker:
    """Deterministic relevance: query-term coverage (plain and IDF-weighted across the
    candidate set), vector similarity, title/section match and source authority."""

    name = "heuristic"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def rerank(self, query: str, key_terms: list[str], candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        keys = set(key_terms) or term_set(query)
        s = self.settings
        term_cache = {c.chunk_id: chunk_terms(c) for c in candidates}
        n = len(candidates)
        idf = {
            t: math.log((n + 1) / (sum(1 for c in candidates if t in term_cache[c.chunk_id]) + 0.5)) + 1 for t in keys
        }
        total_idf = sum(idf.values()) or 1.0
        for chunk in candidates:
            terms = term_cache[chunk.chunk_id]
            matched = keys & terms
            plain = len(matched) / len(keys) if keys else 0.0
            weighted = sum(idf[t] for t in matched) / total_idf if keys else 0.0
            title_terms = term_set(f"{chunk.document_title} {chunk.heading_path or ''}")
            title = len(keys & title_terms) / len(keys) if keys else 0.0
            chunk.coverage = plain
            chunk.matched_terms = sorted(matched)
            chunk.rerank_score = (
                s.rerank_weight_coverage * (plain + weighted) / 2
                + s.rerank_weight_vector * min(1.0, chunk.vector_similarity)
                + s.rerank_weight_title * title
                + s.rerank_weight_authority * s.authority_weight(chunk.authority)
            )
        return deduplicate(sorted(candidates, key=lambda c: c.rerank_score, reverse=True), s)


def deduplicate(ranked: list[RetrievedChunk], settings: Settings) -> list[RetrievedChunk]:
    kept: list[RetrievedChunk] = []
    kept_terms: list[set[str]] = []
    hashes: set[str] = set()
    for chunk in ranked:
        terms = term_set(chunk.content)
        if chunk.content_hash in hashes or any(
            jaccard(terms, other) >= settings.rag_dedup_similarity for other in kept_terms
        ):
            continue
        hashes.add(chunk.content_hash)
        kept.append(chunk)
        kept_terms.append(terms)
    return kept


class _Score(BaseModel):
    id: str
    relevance: float


class _Scores(BaseModel):
    scores: list[_Score]


class LLMReranker:
    """Blends LLM relevance judgments with the heuristic score; falls back to the
    heuristic if the LLM call fails, so reranking never blocks an answer."""

    name = "llm"
    _MAX_CANDIDATES = 12

    def __init__(self, llm: StructuredLLM, heuristic: HeuristicReranker) -> None:
        self.llm = llm
        self.heuristic = heuristic

    async def rerank(self, query: str, key_terms: list[str], candidates: list[RetrievedChunk]) -> list[RetrievedChunk]:
        ranked = await self.heuristic.rerank(query, key_terms, candidates)
        top = ranked[: self._MAX_CANDIDATES]
        if not top:
            return ranked
        passages = "\n".join(
            f'<passage id="P{i}">{truncate(c.content, 1200).replace("<", "&lt;")}</passage>' for i, c in enumerate(top)
        )
        user = f"<question>{query.replace('<', '&lt;')}</question>\n<passages>\n{passages}\n</passages>"
        try:
            result = await self.llm.generate(prompt=get_prompt("reranker"), user=user, output_model=_Scores)
        except LLMError as exc:
            log.warning("llm_rerank_failed_fallback_heuristic", error=exc.detail)
            return ranked
        scores = {s.id: min(1.0, max(0.0, s.relevance)) for s in result.value.scores}
        for i, chunk in enumerate(top):
            if f"P{i}" in scores:
                chunk.rerank_score = 0.5 * chunk.rerank_score + 0.5 * scores[f"P{i}"]
        return sorted(ranked, key=lambda c: c.rerank_score, reverse=True)

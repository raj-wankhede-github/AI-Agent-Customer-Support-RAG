"""Evidence selection and sufficiency: decide, before any generation, whether the
retrieved material can possibly support an answer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings
from app.rag.reranker import chunk_terms
from app.rag.types import RetrievedChunk
from app.utils.text import estimate_tokens


@dataclass
class SufficiencyResult:
    sufficient: bool
    reason_code: str | None
    detail: str
    selected: list[RetrievedChunk] = field(default_factory=list)
    top_score: float = 0.0
    coverage: float = 0.0
    missing_terms: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "sufficient": self.sufficient,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "top_score": round(self.top_score, 4),
            "coverage": round(self.coverage, 4),
            "missing_terms": self.missing_terms,
            "selected_count": len(self.selected),
        }


def select_evidence(
    candidates: list[RetrievedChunk],
    key_terms: list[str],
    settings: Settings,
    required_terms: list[str] | None = None,
) -> SufficiencyResult:
    """`required_terms` come from specific entities in the question (a product, place or
    person). Evidence that never mentions them cannot answer the question, however well it
    covers the generic words ("How long does shipping to Antarctica take?")."""
    if not candidates:
        return SufficiencyResult(False, "NO_RELEVANT_EVIDENCE", "No knowledge-base content matched the question.")
    top = candidates[0].rerank_score
    passing = [c for c in candidates if c.rerank_score >= settings.rag_min_rerank_score]
    if not passing:
        return SufficiencyResult(
            False,
            "LOW_RELEVANCE",
            f"No sufficiently relevant knowledge-base evidence was found "
            f"(best relevance {top:.2f} < threshold {settings.rag_min_rerank_score:.2f}).",
            top_score=top,
        )

    selected: list[RetrievedChunk] = []
    budget = settings.rag_max_context_tokens
    for chunk in passing:
        tokens = estimate_tokens(chunk.content)
        if len(selected) >= settings.rag_max_context or (selected and tokens > budget):
            break
        selected.append(chunk)
        budget -= tokens

    keys = set(key_terms)
    evidence_terms: set[str] = set()
    for chunk in selected:
        evidence_terms |= chunk_terms(chunk)
    covered = keys & evidence_terms
    coverage = len(covered) / len(keys) if keys else 0.0
    missing = sorted(keys - covered)
    missing_required = sorted(set(required_terms or []) - evidence_terms)
    if missing_required:
        return SufficiencyResult(
            False,
            "MISSING_ENTITY",
            f"The evidence never mentions what the question is specifically about ({', '.join(missing_required)}).",
            selected=selected,
            top_score=top,
            coverage=coverage,
            missing_terms=sorted(set(missing) | set(missing_required)),
        )
    semantic_override = (
        settings.rag_semantic_override_similarity is not None
        and max(c.vector_similarity for c in selected) >= settings.rag_semantic_override_similarity
    )
    if coverage < settings.rag_min_query_coverage and not semantic_override:
        return SufficiencyResult(
            False,
            "INSUFFICIENT_COVERAGE",
            "Relevant-looking evidence was found, but it does not cover the question "
            f"(coverage {coverage:.2f}; not found: {', '.join(missing) or 'n/a'}).",
            selected=selected,
            top_score=top,
            coverage=coverage,
            missing_terms=missing,
        )
    return SufficiencyResult(
        True,
        None,
        f"{len(selected)} evidence chunk(s) selected (coverage {coverage:.2f}).",
        selected=selected,
        top_score=top,
        coverage=coverage,
        missing_terms=missing,
    )

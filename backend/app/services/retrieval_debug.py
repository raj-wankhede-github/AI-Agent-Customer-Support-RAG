"""Admin retrieval debugging: runs query understanding, retrieval, reranking, sufficiency
and conflict detection for a query - without generating an answer - so admins can see
exactly why the knowledge base does or does not support a question."""

from __future__ import annotations

from datetime import date

from app.core.container import Container
from app.rag.conflicts import detect_conflicts, resolve_conflicts
from app.rag.evidence import select_evidence
from app.rag.query_analysis import entity_terms
from app.rag.types import ConversationContext, RetrievalFilters
from app.schemas.admin import RetrievalDebugRequest, RetrievalDebugResponse
from app.security.principal import Principal


async def debug_retrieval(
    container: Container, principal: Principal, body: RetrievalDebugRequest
) -> RetrievalDebugResponse:
    agent = container.agent
    analysis = await agent.analyzer.analyze(body.query, ConversationContext())
    retrieval = await container.retriever.retrieve(
        company_id=principal.company_id,
        query=analysis.standalone_query,
        filters=RetrievalFilters(product=body.product, category=body.category),
    )
    ranked = await agent.reranker.rerank(analysis.standalone_query, analysis.key_terms, retrieval.candidates)
    sufficiency = select_evidence(
        ranked, analysis.key_terms, container.settings, required_terms=entity_terms(analysis.entities)
    )
    conflicts = detect_conflicts(sufficiency.selected, analysis.key_terms, container.settings)
    kept, conflicts = resolve_conflicts(conflicts, sufficiency.selected, container.settings, date.today())
    return RetrievalDebugResponse(
        query=body.query,
        standalone_query=analysis.standalone_query,
        key_terms=analysis.key_terms,
        lexical_query=retrieval.lexical_query,
        embedding_model=retrieval.embedding_model,
        candidates=[c.debug_view() for c in ranked],
        selected_evidence=[c.debug_view() for c in kept],
        sufficiency=sufficiency.as_dict(),
        conflicts=[c.as_dict() for c in conflicts],
    )

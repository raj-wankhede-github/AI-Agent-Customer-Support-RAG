"""Composition root: builds the object graph from settings.

Used by the API, the worker, the CLI and the evaluation runner, so all of them run the
exact same pipeline. Tests pass overrides (e.g. a fake LLM provider) instead of patching.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.agents.support_agent import SupportAgent
from app.core.config import Settings
from app.db.session import create_engine, create_sessionmaker
from app.ingestion.pipeline import IngestionPipeline
from app.llm.base import LLMProvider
from app.llm.factory import create_llm_provider
from app.llm.prompts import derive_canary, load_prompts
from app.llm.structured import StructuredLLM
from app.rag.context import ConversationSummarizer
from app.rag.embeddings.base import EmbeddingProvider
from app.rag.embeddings.factory import create_embedding_provider
from app.rag.generator import AnswerGenerator, ExtractiveAnswerGenerator, LLMAnswerGenerator
from app.rag.grounding import GroundingValidator, LLMGroundingJudge
from app.rag.query_analysis import LLMQueryAnalyzer, QueryAnalyzer, RuleBasedQueryAnalyzer
from app.rag.reranker import HeuristicReranker, LLMReranker, Reranker
from app.rag.retriever import PgHybridRetriever
from app.rag.types import Retriever
from app.security.rate_limit import InMemoryRateLimiter, RateLimiterBackend
from app.storage.base import ObjectStorage
from app.storage.factory import create_storage

_UNSET = object()


@dataclass
class Container:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    storage: ObjectStorage
    embedder: EmbeddingProvider
    llm_provider: LLMProvider | None
    retriever: Retriever
    agent: SupportAgent
    summarizer: ConversationSummarizer
    pipeline: IngestionPipeline
    rate_limiter: RateLimiterBackend
    # Set by the API lifespan when the ingestion worker runs in-process.
    worker: Any = None

    async def aclose(self) -> None:
        await self.engine.dispose()


def build_container(
    settings: Settings,
    *,
    engine: AsyncEngine | None = None,
    llm_provider: LLMProvider | object | None = _UNSET,
    embedder: EmbeddingProvider | None = None,
    storage: ObjectStorage | None = None,
    retriever: Retriever | None = None,
) -> Container:
    load_prompts()  # fail fast on malformed prompt files
    engine = engine or create_engine(settings)
    sessions = create_sessionmaker(engine)
    storage = storage or create_storage(settings)
    embedder = embedder or create_embedding_provider(settings)
    provider = create_llm_provider(settings) if llm_provider is _UNSET else llm_provider
    assert provider is None or hasattr(provider, "complete_json")
    llm = StructuredLLM(provider) if provider is not None else None  # type: ignore[arg-type]
    canary = derive_canary(settings.jwt_secret.get_secret_value())

    rules = RuleBasedQueryAnalyzer(settings)
    analyzer: QueryAnalyzer = LLMQueryAnalyzer(llm, rules, settings) if llm else rules
    heuristic = HeuristicReranker(settings)
    reranker: Reranker = LLMReranker(llm, heuristic) if llm and settings.reranker_provider == "llm" else heuristic
    generator: AnswerGenerator = LLMAnswerGenerator(llm, settings, canary) if llm else ExtractiveAnswerGenerator()
    retriever = retriever or PgHybridRetriever(sessions, embedder, settings)

    agent = SupportAgent(
        settings=settings,
        analyzer=analyzer,
        retriever=retriever,
        reranker=reranker,
        generator=generator,
        validator=GroundingValidator(settings, canary, known_entities={settings.app_name}),
        judge=LLMGroundingJudge(llm) if llm else None,
        embedding_model=embedder.model,
    )
    return Container(
        settings=settings,
        engine=engine,
        sessions=sessions,
        storage=storage,
        embedder=embedder,
        llm_provider=provider,  # type: ignore[arg-type]
        retriever=retriever,
        agent=agent,
        summarizer=ConversationSummarizer(settings, llm),
        pipeline=IngestionPipeline(sessions, storage, embedder, settings),
        rate_limiter=InMemoryRateLimiter(),
    )

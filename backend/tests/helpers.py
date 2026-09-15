"""Test doubles and builders. Nothing here is used by the application."""

from __future__ import annotations

import html
import json
import re
import uuid
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from typing import Any

from app.agents.support_agent import SupportAgent
from app.core.config import Settings
from app.core.errors import LLMError, RetrievalError
from app.llm.base import LLMResponse, LLMUsage
from app.llm.prompts import derive_canary
from app.llm.structured import StructuredLLM
from app.rag.embeddings.base import embedding_input
from app.rag.embeddings.hashing import HashingEmbeddingProvider
from app.rag.generator import ExtractiveAnswerGenerator, LLMAnswerGenerator
from app.rag.grounding import GroundingValidator, LLMGroundingJudge
from app.rag.query_analysis import LLMQueryAnalyzer, RuleBasedQueryAnalyzer
from app.rag.reranker import HeuristicReranker
from app.rag.types import RetrievalFilters, RetrievalResult, RetrievedChunk
from app.utils.hashing import sha256_text
from app.utils.text import split_sentences, term_set

COMPANY_ID = uuid.UUID("00000000-0000-0000-0000-00000000ac3e")


def make_chunk(
    title: str,
    content: str,
    *,
    section: str | None = None,
    authority: str = "OFFICIAL_POLICY",
    effective_date: date | None = None,
    document_id: uuid.UUID | None = None,
    page: int | None = None,
    product: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid.uuid4(),
        document_id=document_id or uuid.uuid5(uuid.NAMESPACE_URL, title),
        document_version_id=uuid.uuid4(),
        document_title=title,
        version_number=1,
        source_type="MARKDOWN",
        source_uri=None,
        authority=authority,
        category=None,
        product=product,
        locale="en-US",
        effective_date=effective_date,
        chunk_index=0,
        content=content,
        content_hash=sha256_text(content),
        section_title=section,
        heading_path=f"{title} > {section}" if section else title,
        page_number=page,
        page_end=page,
        metadata=metadata or {},
    )


ACME_CHUNKS = [
    make_chunk(
        "Shipping Policy",
        "This policy explains how Acme ships orders placed on acme.example.",
        section="Acme Shipping Policy",
        page=1,
    ),
    make_chunk(
        "Shipping Policy",
        "Standard shipping takes 3-5 business days after your order ships. Standard shipping is free for orders of $50 or more.",
        section="Standard Shipping",
        page=1,
    ),
    make_chunk(
        "Shipping Policy",
        "Expedited shipping takes 1-2 business days after your order ships and costs $14.99.",
        section="Expedited Shipping",
        page=1,
    ),
    make_chunk(
        "Shipping Policy",
        "International orders typically arrive within 7-14 business days after they ship.",
        section="International Shipping",
        page=2,
    ),
    make_chunk(
        "Refund and Returns Policy",
        "You can return most items within 30 days of delivery for a full refund. Items must be unused and in their original packaging.",
        section="Return Window",
    ),
    make_chunk(
        "Refund and Returns Policy",
        "International purchases can be returned within 45 days of delivery. Customers are responsible for return shipping costs on international purchases. Original shipping charges and customs duties are non-refundable.",
        section="International Purchases",
    ),
    make_chunk(
        "Account FAQ",
        "Select Forgot password on the sign-in page and enter the email address on your account. The reset link expires after 60 minutes.",
        section="How do I reset my password?",
        authority="FAQ",
    ),
    make_chunk(
        "Support Contact Policy",
        "Acme Support is available Monday to Friday from 8:00 a.m. to 8:00 p.m. Eastern Time.",
        section="SUPPORT HOURS",
        authority="OFFICIAL_DOCUMENTATION",
    ),
]


class InMemoryRetriever:
    """Hybrid-ish retrieval over in-memory chunks, mirroring PgHybridRetriever's contract."""

    def __init__(self, chunks: list[RetrievedChunk], min_score: float = 0.15) -> None:
        self.chunks = chunks
        self.embedder = HashingEmbeddingProvider(384)
        self.min_score = min_score
        self.calls = 0

    async def retrieve(self, *, company_id: uuid.UUID, query: str, filters: RetrievalFilters) -> RetrievalResult:
        self.calls += 1
        query_vector = await self.embedder.embed_query(query)
        query_terms = term_set(query)
        scored = []
        for chunk in self.chunks:
            if filters.authorities and chunk.authority not in filters.authorities:
                continue
            vector = await self.embedder.embed_query(
                embedding_input(chunk.document_title, chunk.heading_path, chunk.content)
            )
            similarity = sum(a * b for a, b in zip(query_vector, vector, strict=True))
            overlap = len(query_terms & term_set(f"{chunk.heading_path} {chunk.content}"))
            if similarity < self.min_score and overlap == 0:
                continue
            scored.append((similarity, overlap, replace(chunk, vector_similarity=max(0.0, similarity))))
        by_vector = sorted(scored, key=lambda s: s[0], reverse=True)
        by_lexical = sorted((s for s in scored if s[1] > 0), key=lambda s: s[1], reverse=True)
        candidates = []
        for rank, (_, _, chunk) in enumerate(by_vector, start=1):
            chunk.vector_rank = rank
            chunk.fused_score = 1 / (60 + rank)
            candidates.append(chunk)
        for rank, (_, overlap, chunk) in enumerate(by_lexical, start=1):
            chunk.lexical_rank = rank
            chunk.lexical_score = float(overlap)
            chunk.fused_score += 1 / (60 + rank)
        candidates.sort(key=lambda c: c.fused_score, reverse=True)
        return RetrievalResult(candidates, self.embedder.model, " | ".join(sorted(query_terms)))


class FailingRetriever:
    calls = 0

    async def retrieve(self, *, company_id: uuid.UUID, query: str, filters: RetrievalFilters) -> RetrievalResult:
        self.calls += 1
        raise RetrievalError(detail="database unavailable")


Handler = Callable[[str, str], Any] | dict[str, Any] | str | BaseException


class ScriptedLLMProvider:
    """Fake provider: responses are scripted per prompt (schema) name. A list of handlers is
    consumed in order; its last entry repeats."""

    name = "fake"
    model = "fake-model"

    def __init__(self, handlers: dict[str, Handler | list[Handler]]) -> None:
        self.handlers = handlers
        self.calls: list[tuple[str, str]] = []

    async def complete_json(
        self, *, system: str, user: str, schema: dict[str, Any], schema_name: str, max_tokens: int | None = None
    ) -> LLMResponse:
        self.calls.append((schema_name, user))
        handler = self.handlers.get(schema_name)
        if handler is None:
            raise LLMError(detail=f"no scripted response for {schema_name}", transient=False)
        if isinstance(handler, list):
            handler = handler.pop(0) if len(handler) > 1 else handler[0]
        if isinstance(handler, BaseException):
            raise handler
        result = handler(system, user) if callable(handler) else handler
        text = result if isinstance(result, str) else json.dumps(result)
        return LLMResponse(text=text, model=self.model, latency_ms=1, usage=LLMUsage(100, 20))

    def count(self, schema_name: str) -> int:
        return sum(1 for name, _ in self.calls if name == schema_name)


def understanding_passthrough(system: str, user: str) -> dict[str, Any]:
    message = html.unescape(re.search(r"<customer_message>(.*?)</customer_message>", user, re.S).group(1))  # type: ignore[union-attr]
    return {
        "intent": "QUESTION", "standalone_query": message, "needs_context": False, "requires_clarification": False,
        "clarification_question": None, "entities": [], "question_type": "FACTUAL", "out_of_domain": False,
        "frustration": False, "disputes_previous_answer": False, "sensitive_category": "none", "handoff_requested": False,
    }  # fmt: skip


def first_source_sentence(user: str) -> tuple[str, str]:
    match = re.search(r'<source id="(S\d+)"[^>]*>\n(.*?)\n</source>', user, re.S)
    assert match, "prompt contains no evidence sources"
    return match.group(1), split_sentences(html.unescape(match.group(2)))[0]


def grounded_answer(system: str, user: str) -> dict[str, Any]:
    source_id, sentence = first_source_sentence(user)
    return answer_payload(sentence, [source_id])


def answer_payload(text: str, evidence_ids: list[str], **overrides: Any) -> dict[str, Any]:
    payload = {
        "should_abstain": False, "abstain_reason": None, "answer": text,
        "claims": [{"text": text, "evidence_ids": evidence_ids}] if text else [],
        "conflict_detected": False, "handoff_required": False, "handoff_reason": None,
    }  # fmt: skip
    payload.update(overrides)
    return payload


def judge_all_supported(system: str, user: str) -> dict[str, Any]:
    count = len(re.findall(r"<claim index=", user))
    return {"verdicts": [{"claim_index": i, "supported": True, "reason": None} for i in range(count)]}


def default_llm_handlers() -> dict[str, Handler | list[Handler]]:
    return {
        "query_understanding": understanding_passthrough,
        "answer_generator": grounded_answer,
        "grounding_validator": judge_all_supported,
    }


def build_agent(
    settings: Settings,
    chunks: list[RetrievedChunk] | None = None,
    *,
    provider: ScriptedLLMProvider | None = None,
    retriever: Any = None,
) -> SupportAgent:
    llm = StructuredLLM(provider) if provider else None
    canary = derive_canary(settings.jwt_secret.get_secret_value())
    rules = RuleBasedQueryAnalyzer(settings)
    return SupportAgent(
        settings=settings,
        analyzer=LLMQueryAnalyzer(llm, rules, settings) if llm else rules,
        retriever=retriever or InMemoryRetriever(chunks if chunks is not None else ACME_CHUNKS),
        reranker=HeuristicReranker(settings),
        generator=LLMAnswerGenerator(llm, settings, canary) if llm else ExtractiveAnswerGenerator(),
        validator=GroundingValidator(settings, canary, known_entities={settings.app_name}),
        judge=LLMGroundingJudge(llm) if llm else None,
        embedding_model="hashing-v1",
    )

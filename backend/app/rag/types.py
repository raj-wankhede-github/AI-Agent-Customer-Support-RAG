"""Data structures shared across the RAG pipeline."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from app.models.enums import MessageRole


@dataclass
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    document_version_id: uuid.UUID
    document_title: str
    version_number: int
    source_type: str
    source_uri: str | None
    authority: str
    category: str | None
    product: str | None
    locale: str | None
    effective_date: date | None
    chunk_index: int
    content: str
    content_hash: str
    section_title: str | None
    heading_path: str | None
    page_number: int | None
    page_end: int | None
    metadata: dict[str, Any] = field(default_factory=dict)
    vector_similarity: float = 0.0
    lexical_score: float = 0.0
    vector_rank: int | None = None
    lexical_rank: int | None = None
    fused_score: float = 0.0
    rerank_score: float = 0.0
    coverage: float = 0.0
    matched_terms: list[str] = field(default_factory=list)

    @property
    def injection_flags(self) -> list[str]:
        flags = self.metadata.get("injection_flags") or []
        return [str(f) for f in flags]

    def debug_view(self) -> dict[str, Any]:
        return {
            "chunk_id": str(self.chunk_id),
            "document_id": str(self.document_id),
            "document_title": self.document_title,
            "version": self.version_number,
            "authority": self.authority,
            "section_title": self.section_title,
            "page_number": self.page_number,
            "vector_similarity": round(self.vector_similarity, 4),
            "lexical_score": round(self.lexical_score, 4),
            "vector_rank": self.vector_rank,
            "lexical_rank": self.lexical_rank,
            "rerank_score": round(self.rerank_score, 4),
            "coverage": round(self.coverage, 4),
            "matched_terms": self.matched_terms,
            "excerpt": self.content[:280],
        }


@dataclass
class RetrievalFilters:
    product: str | None = None
    category: str | None = None
    locale: str | None = None
    authorities: list[str] | None = None


@dataclass
class RetrievalResult:
    candidates: list[RetrievedChunk]
    embedding_model: str
    lexical_query: str


class Retriever(Protocol):
    async def retrieve(self, *, company_id: uuid.UUID, query: str, filters: RetrievalFilters) -> RetrievalResult: ...


# --- Conversation ------------------------------------------------------------------


@dataclass
class HistoryTurn:
    role: MessageRole
    content: str
    standalone_query: str | None = None
    answer_status: str | None = None


@dataclass
class ConversationContext:
    summary: str | None = None
    recent: list[HistoryTurn] = field(default_factory=list)
    consecutive_failed_answers: int = 0

    def last_user_query(self) -> str | None:
        for turn in reversed(self.recent):
            if turn.role is MessageRole.USER:
                return turn.standalone_query or turn.content
        return None

    def last_assistant_turn(self) -> HistoryTurn | None:
        for turn in reversed(self.recent):
            if turn.role in (MessageRole.ASSISTANT, MessageRole.HUMAN_AGENT):
                return turn
        return None


# --- Query understanding -----------------------------------------------------------


class Intent(StrEnum):
    QUESTION = "QUESTION"
    GREETING = "GREETING"
    THANKS = "THANKS"
    HANDOFF_REQUEST = "HANDOFF_REQUEST"
    ACCOUNT_ACTION = "ACCOUNT_ACTION"
    OTHER = "OTHER"


QuestionType = Literal["FACTUAL", "PROCEDURAL", "POLICY", "TROUBLESHOOTING", "ACCOUNT", "OTHER"]
SensitiveCategory = Literal[
    "none", "legal", "financial", "medical", "security_incident", "identity_verification", "account_ownership"
]


class QueryUnderstanding(BaseModel):
    """What the LLM (or rule engine) returns. Also the LLM output schema."""

    intent: Intent
    standalone_query: str
    needs_context: bool
    requires_clarification: bool
    clarification_question: str | None
    entities: list[str]
    question_type: QuestionType
    out_of_domain: bool
    frustration: bool
    disputes_previous_answer: bool
    sensitive_category: SensitiveCategory
    handoff_requested: bool


class QueryAnalysis(QueryUnderstanding):
    """QueryUnderstanding plus deterministic signals that an LLM cannot override."""

    injection_flags: list[str] = []
    key_terms: list[str] = []
    analyzer: str = "rules"


# --- Generation ----------------------------------------------------------------------


class Claim(BaseModel):
    text: str
    evidence_ids: list[str]


class GeneratedAnswer(BaseModel):
    should_abstain: bool
    abstain_reason: str | None
    answer: str
    claims: list[Claim]
    conflict_detected: bool
    handoff_required: bool
    handoff_reason: str | None


@dataclass
class EvidenceItem:
    evidence_id: str
    chunk: RetrievedChunk

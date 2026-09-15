from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import HandoffPriority, HandoffStatus, UserRole
from app.schemas.conversation import ConversationOut, MessageOut


class UserRef(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    role: UserRole


class HandoffOut(BaseModel):
    id: uuid.UUID
    conversation_id: uuid.UUID
    conversation_title: str
    customer: UserRef
    reason_code: str
    reason_detail: str
    priority: HandoffPriority
    status: HandoffStatus
    assigned_agent: UserRef | None
    triggering_message_id: uuid.UUID | None
    triggering_message_preview: str | None
    created_at: datetime
    assigned_at: datetime | None
    resolved_at: datetime | None
    waiting_seconds: int


class AdminConversationOut(ConversationOut):
    customer: UserRef
    assigned_agent: UserRef | None
    message_count: int


class TraceOut(BaseModel):
    id: uuid.UUID
    user_message_id: uuid.UUID
    assistant_message_id: uuid.UUID | None
    original_query: str
    standalone_query: str
    decision: str
    decision_reason: str | None
    handoff_reason: str | None
    analysis: dict[str, Any]
    candidates: list[dict[str, Any]]
    selected_evidence: list[dict[str, Any]]
    sufficiency: dict[str, Any]
    conflicts: list[dict[str, Any]]
    validation: dict[str, Any]
    confidence: dict[str, Any]
    timings_ms: dict[str, Any]
    prompt_versions: dict[str, Any]
    provider: str | None
    model: str | None
    embedding_model: str | None
    input_tokens: int
    output_tokens: int
    created_at: datetime


class AdminConversationDetail(BaseModel):
    conversation: AdminConversationOut
    messages: list[MessageOut]
    handoffs: list[HandoffOut]
    traces: list[TraceOut]


class AgentReplyRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class AssignRequest(BaseModel):
    agent_id: uuid.UUID | None = Field(default=None, description="Defaults to the calling agent")


class RetrievalDebugRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    product: str | None = None
    category: str | None = None


class RetrievalDebugResponse(BaseModel):
    query: str
    standalone_query: str
    key_terms: list[str]
    lexical_query: str
    embedding_model: str
    candidates: list[dict[str, Any]]
    selected_evidence: list[dict[str, Any]]
    sufficiency: dict[str, Any]
    conflicts: list[dict[str, Any]]


class CountItem(BaseModel):
    key: str
    count: int


class UnansweredQuestion(BaseModel):
    trace_id: uuid.UUID
    conversation_id: uuid.UUID
    question: str
    decision: str
    reason: str | None
    created_at: datetime


class MetricsOut(BaseModel):
    window_days: int
    conversations_total: int
    conversations_open: int
    conversations_waiting_for_human: int
    conversations_resolved: int
    conversations_closed: int
    assistant_responses: int
    answered: int
    abstained: int
    handoff_responses: int
    abstention_rate: float
    handoff_rate: float = Field(description="Share of conversations with at least one handoff")
    pending_handoffs: int
    avg_response_latency_ms: float | None
    p95_response_latency_ms: float | None
    feedback_helpful: int
    feedback_not_helpful: int
    feedback_reasons: list[CountItem]
    documents_active: int
    documents_inactive: int
    documents_searchable: int
    versions_by_status: list[CountItem]
    ingestion_failures: int
    retrieval_success_rate: float | None
    citation_validation_failures: int
    low_confidence_responses: int
    handoff_reasons: list[CountItem]
    abstention_reasons: list[CountItem]
    recent_unanswered: list[UnansweredQuestion]
    input_tokens: int
    output_tokens: int


class AuditLogOut(BaseModel):
    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    action: str
    target_type: str | None
    target_id: str | None
    request_id: str | None
    details: dict[str, Any]
    created_at: datetime

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    AnswerStatus,
    ConfidenceLevel,
    ConversationStatus,
    FeedbackRating,
    FeedbackReason,
    HandoffStatus,
    MessageRole,
    UserRole,
)


class ActorRef(BaseModel):
    id: uuid.UUID
    name: str = Field(description="Display name; staff appear as 'First (Support)' in customer-facing responses")
    role: UserRole


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    first_message: str | None = Field(
        default=None,
        max_length=4000,
        description=(
            "The opening question the customer is about to send. If a conversation of theirs that is not closed "
            "and was active within CONVERSATION_REUSE_WINDOW_HOURS started with the same question (or has no "
            "messages yet), that conversation is returned instead of creating a duplicate."
        ),
    )


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    status: ConversationStatus
    handoff_status: HandoffStatus
    last_message_preview: str | None
    last_message_at: datetime
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    closed_by: ActorRef | None = Field(
        default=None, description="Who closed the conversation (null if open, closed automatically, or user removed)"
    )
    closed_automatically: bool = Field(default=False, description="Closed by the resolved-conversation timeout")
    resolved_at: datetime | None = None
    resolved_by: ActorRef | None = None
    reopened_at: datetime | None = Field(default=None, description="Last time a customer reply reopened it")
    reopen_count: int = 0


class ConversationCreated(ConversationOut):
    reused: bool = Field(
        default=False, description="True when an existing conversation was returned instead of creating a new one"
    )


class Citation(BaseModel):
    evidence_id: str
    chunk_id: str
    document_id: str
    document_version_id: str
    document_title: str
    version: int
    source_type: str
    source_uri: str | None = None
    authority: str
    section_title: str | None = None
    heading_path: str | None = None
    page_number: int | None = None
    chunk_index: int
    effective_date: str | None = None
    excerpt: str


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    rating: FeedbackRating
    reason: str | None
    comment: str | None


class MessageOut(BaseModel):
    id: uuid.UUID
    role: MessageRole
    content: str
    created_at: datetime
    answer_status: AnswerStatus | None = None
    response_kind: str | None = None
    confidence: ConfidenceLevel | None = None
    citations: list[Citation] = []
    handoff_reason: str | None = None
    author_name: str | None = None
    feedback: FeedbackOut | None = None


class ConversationDetail(BaseModel):
    conversation: ConversationOut
    messages: list[MessageOut]


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    client_message_id: str | None = Field(
        default=None,
        max_length=100,
        pattern=r"^[A-Za-z0-9._:-]+$",
        description="Idempotency key. Retrying with the same key returns the original result instead of re-running the agent.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"content": "What is your standard shipping time?", "client_message_id": "c1f7-0001"}
        }
    )


class ChatResponse(BaseModel):
    conversation_id: uuid.UUID
    user_message_id: uuid.UUID
    message_id: uuid.UUID | None = Field(description="Assistant message id; null while a human owns the conversation")
    answer: str | None
    citations: list[Citation]
    answer_status: AnswerStatus
    confidence: ConfidenceLevel | None
    handoff_required: bool
    handoff_reason: str | None
    conversation_status: ConversationStatus
    message: MessageOut | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "conversation_id": "0b6b0f0e-8f2e-4d8a-9d8e-0d6f4b1f7c11",
                "user_message_id": "a2f0b0a4-2c1d-4f3e-8a9b-1c2d3e4f5a6b",
                "message_id": "7d3e4f5a-6b7c-4d8e-9f0a-1b2c3d4e5f60",
                "answer": "Standard shipping takes 3-5 business days after your order ships.",
                "citations": [
                    {
                        "evidence_id": "S1",
                        "chunk_id": "…",
                        "document_id": "…",
                        "document_version_id": "…",
                        "document_title": "Shipping Policy",
                        "version": 1,
                        "source_type": "PDF",
                        "authority": "OFFICIAL_POLICY",
                        "section_title": "Standard Shipping",
                        "page_number": 1,
                        "chunk_index": 1,
                        "excerpt": "Standard shipping takes 3-5 business days after your order ships.",
                    }
                ],
                "answer_status": "ANSWERED",
                "confidence": "HIGH",
                "handoff_required": False,
                "handoff_reason": None,
                "conversation_status": "OPEN",
            }
        }
    )


class FeedbackRequest(BaseModel):
    message_id: uuid.UUID
    rating: FeedbackRating
    reason: FeedbackReason | None = None
    comment: str | None = Field(default=None, max_length=1000)


class HandoffRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


def message_out(message: Any, *, author_name: str | None = None, feedback: Any = None) -> MessageOut:
    metadata = message.retrieval_metadata or {}
    return MessageOut(
        id=message.id,
        role=message.role,
        content=message.content,
        created_at=message.created_at,
        answer_status=message.answer_status,
        response_kind=metadata.get("response_kind"),
        confidence=message.confidence,
        citations=[Citation.model_validate(c) for c in (message.citations or [])],
        handoff_reason=message.handoff_reason,
        author_name=author_name,
        feedback=FeedbackOut.model_validate(feedback) if feedback else None,
    )

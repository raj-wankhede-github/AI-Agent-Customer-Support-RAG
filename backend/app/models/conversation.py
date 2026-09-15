from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAt, Timestamps, UUIDPrimaryKey, utcnow
from app.models.enums import (
    AnswerStatus,
    ConfidenceLevel,
    ConversationStatus,
    FeedbackRating,
    HandoffPriority,
    HandoffStatus,
    MessageRole,
)


class Conversation(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("ix_conversations_owner_recent", "company_id", "user_id", "last_message_at"),
        Index("ix_conversations_company_status", "company_id", "status", "last_message_at"),
        # Drives the automatic close of resolved conversations.
        Index("ix_conversations_resolved_due", "resolved_at", postgresql_where=text("status = 'RESOLVED'")),
        Index(
            "ix_conversations_title_fts",
            text("to_tsvector('simple', title)"),
            postgresql_using="gin",
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    status: Mapped[ConversationStatus] = mapped_column(String(30), default=ConversationStatus.OPEN)
    handoff_status: Mapped[HandoffStatus] = mapped_column(String(20), default=HandoffStatus.NONE)
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # Rolling summary of turns older than the recent-history window. Conversation memory
    # only: it is never treated as a source of company facts.
    summary: Mapped[str | None] = mapped_column(Text)
    summarized_message_count: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_failed_answers: Mapped[int] = mapped_column(Integer, default=0)
    last_message_preview: Mapped[str | None] = mapped_column(String(240))
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now()
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # True when closed by the resolved-conversation timeout rather than by a person.
    closed_automatically: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    # Set when a customer replies to a resolved conversation, which reopens it.
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopen_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Message(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
        Index("ix_messages_company_role_created", "company_id", "role", "created_at"),
        Index(
            "ix_messages_content_fts",
            text("to_tsvector('simple', content)"),
            postgresql_using="gin",
        ),
        UniqueConstraint("conversation_id", "client_message_id", name="uq_messages_idempotency"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    role: Mapped[MessageRole] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    client_message_id: Mapped[str | None] = mapped_column(String(100))
    reply_to_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL")
    )
    answer_status: Mapped[AnswerStatus | None] = mapped_column(String(30))
    confidence: Mapped[ConfidenceLevel | None] = mapped_column(String(10))
    confidence_score: Mapped[float | None] = mapped_column(Float)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, server_default="[]")
    # Concise structured audit metadata (retrieval_count, top_score, selected_sources,
    # validation_passed, abstention_reason, prompt_version, ...). No chain-of-thought.
    retrieval_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default="{}")
    handoff_reason: Mapped[str | None] = mapped_column(String(50))
    provider: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str | None] = mapped_column(String(200))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)


class Feedback(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "feedback"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_feedback_message_user"),
        Index("ix_feedback_company_created", "company_id", "created_at"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    message_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"))
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    rating: Mapped[FeedbackRating] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(String(30))
    comment: Mapped[str | None] = mapped_column(String(1000))


class Handoff(UUIDPrimaryKey, CreatedAt, Base):
    __tablename__ = "handoffs"
    __table_args__ = (
        Index("ix_handoffs_queue", "company_id", "status", "created_at"),
        Index("ix_handoffs_conversation", "conversation_id"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    triggering_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="SET NULL")
    )
    reason_code: Mapped[str] = mapped_column(String(50))
    reason_detail: Mapped[str] = mapped_column(String(500))
    priority: Mapped[HandoffPriority] = mapped_column(String(10), default=HandoffPriority.NORMAL)
    status: Mapped[HandoffStatus] = mapped_column(String(20), default=HandoffStatus.PENDING)
    assigned_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RagTrace(UUIDPrimaryKey, CreatedAt, Base):
    """Structured, reconstructable record of why an answer was given or withheld.

    Stores retrieval inputs/outputs, scores, validation results and prompt/model
    versions. Never stores prompts or model chain-of-thought.
    """

    __tablename__ = "rag_traces"
    __table_args__ = (
        Index("ix_rag_traces_company_created", "company_id", "created_at"),
        Index("ix_rag_traces_conversation", "conversation_id"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"))
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE")
    )
    user_message_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE")
    )
    assistant_message_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("messages.id", ondelete="CASCADE"), unique=True
    )
    original_query: Mapped[str] = mapped_column(Text)
    standalone_query: Mapped[str] = mapped_column(Text)
    analysis: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    candidates: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    selected_evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    sufficiency: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    conflicts: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    validation: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    confidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    decision: Mapped[str] = mapped_column(String(30))
    decision_reason: Mapped[str | None] = mapped_column(String(500))
    handoff_reason: Mapped[str | None] = mapped_column(String(50))
    timings_ms: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    prompt_versions: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    provider: Mapped[str | None] = mapped_column(String(50))
    model: Mapped[str | None] = mapped_column(String(100))
    embedding_model: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)

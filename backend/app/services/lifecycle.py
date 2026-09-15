"""Conversation lifecycle transitions shared by customers, staff and background maintenance.

OPEN / WAITING_* --(staff: Mark resolved)--> RESOLVED --(customer replies)--> OPEN (reopened)
RESOLVED --(no customer reply for RESOLVED_AUTO_CLOSE_DAYS)--> CLOSED (automatically)
any --(customer or staff: Close)--> CLOSED (final; no new messages)

Every transition that changes what the customer can do writes a timestamped SYSTEM event
into the transcript and an audit-log entry.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.base import utcnow
from app.models import Conversation, Message
from app.models.enums import ConversationStatus, HandoffStatus, MessageRole, UserRole
from app.repositories import conversations as repo
from app.security.principal import Principal
from app.services.audit import record_audit
from app.utils.text import truncate

log = structlog.get_logger(__name__)


def display_name(name: str, role: UserRole) -> str:
    """Customer-facing name: staff by first name with a (Support) label, customers by full name."""
    if role is UserRole.CUSTOMER:
        return name
    return f"{name.split()[0]} (Support)" if name else "Support team"


def touch(conversation: Conversation, content: str, at: datetime | None = None) -> None:
    moment = at or utcnow()
    conversation.last_message_preview = truncate(re.sub(r"\s+", " ", content), 240)
    conversation.last_message_at = moment
    conversation.updated_at = moment


def add_event(
    session: AsyncSession,
    conversation: Conversation,
    text: str,
    *,
    event: str,
    at: datetime,
    actor_user_id: uuid.UUID | None = None,
) -> Message:
    """A timestamped SYSTEM line in the transcript, visible to the customer and to staff."""
    message = Message(
        company_id=conversation.company_id,
        conversation_id=conversation.id,
        role=MessageRole.SYSTEM,
        content=text,
        author_user_id=actor_user_id,
        citations=[],
        retrieval_metadata={"response_kind": "EVENT", "event": event},
        created_at=at,
    )
    session.add(message)
    touch(conversation, text, at)
    return message


async def close_conversation(
    session: AsyncSession,
    conversation: Conversation,
    actor: Principal | None,
    *,
    automatic_after_days: int | None = None,
) -> bool:
    """Close (final). `actor=None` means the automatic close of an unanswered resolved chat."""
    if conversation.status == ConversationStatus.CLOSED:
        return False
    now = utcnow()
    handoff = await repo.open_handoff(session, conversation.id, lock=True)
    if handoff is not None:
        handoff.status = HandoffStatus.RESOLVED
        handoff.resolved_at = now
        conversation.handoff_status = HandoffStatus.RESOLVED
    conversation.status = ConversationStatus.CLOSED
    conversation.closed_at = now
    conversation.closed_by_user_id = actor.user_id if actor else None
    conversation.closed_automatically = actor is None
    if actor is None:
        text = f"Conversation closed automatically after {automatic_after_days} days without a reply."
    else:
        text = f"Conversation closed by {display_name(actor.name, actor.role)}."
    add_event(session, conversation, text, event="conversation_closed", at=now,
              actor_user_id=actor.user_id if actor else None)  # fmt: skip
    record_audit(
        session,
        action="conversation.closed" if actor else "conversation.auto_closed",
        company_id=conversation.company_id,
        actor_user_id=actor.user_id if actor else None,
        target_type="conversation",
        target_id=conversation.id,
        details={"after_days": automatic_after_days} if actor is None else None,
    )
    return True


async def mark_resolved(session: AsyncSession, conversation: Conversation, actor: Principal) -> bool:
    """Resolve any open handoff and mark the conversation resolved. Idempotent."""
    now = utcnow()
    handoff = await repo.open_handoff(session, conversation.id, lock=True)
    if handoff is not None:
        handoff.status = HandoffStatus.RESOLVED
        handoff.resolved_at = now
        conversation.handoff_status = HandoffStatus.RESOLVED
    if conversation.status in (ConversationStatus.RESOLVED, ConversationStatus.CLOSED):
        return handoff is not None
    conversation.status = ConversationStatus.RESOLVED
    conversation.resolved_at = now
    conversation.resolved_by_user_id = actor.user_id
    add_event(session, conversation, f"Marked resolved by {display_name(actor.name, actor.role)}.",
              event="conversation_resolved", at=now, actor_user_id=actor.user_id)  # fmt: skip
    record_audit(
        session,
        action="conversation.resolved",
        company_id=conversation.company_id,
        actor_user_id=actor.user_id,
        target_type="conversation",
        target_id=conversation.id,
        details={"handoff_id": str(handoff.id) if handoff else None},
    )
    return True


def reopen_if_resolved(session: AsyncSession, conversation: Conversation, actor: Principal, at: datetime) -> bool:
    """A customer writing into a resolved conversation reopens that same conversation."""
    if conversation.status != ConversationStatus.RESOLVED:
        return False
    conversation.status = ConversationStatus.OPEN
    conversation.reopened_at = at
    conversation.reopen_count = (conversation.reopen_count or 0) + 1
    add_event(session, conversation, f"Reopened by {display_name(actor.name, actor.role)}.",
              event="conversation_reopened", at=at, actor_user_id=actor.user_id)  # fmt: skip
    record_audit(
        session,
        action="conversation.reopened",
        company_id=conversation.company_id,
        actor_user_id=actor.user_id,
        target_type="conversation",
        target_id=conversation.id,
        details={"reopen_count": conversation.reopen_count},
    )
    return True


async def auto_close_resolved(
    sessions: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    now: datetime | None = None,
    batch_size: int = 200,
) -> int:
    """Close resolved conversations the customer has not replied to within RESOLVED_AUTO_CLOSE_DAYS.

    Safe with several worker replicas: rows are claimed with FOR UPDATE SKIP LOCKED and the
    status is re-checked inside the same transaction.
    """
    days = settings.resolved_auto_close_days
    if days <= 0:
        return 0
    cutoff = (now or utcnow()) - timedelta(days=days)
    total = 0
    while True:
        async with sessions() as session, session.begin():
            due = (
                await session.execute(
                    select(Conversation)
                    .where(Conversation.status == ConversationStatus.RESOLVED, Conversation.resolved_at < cutoff)
                    .order_by(Conversation.resolved_at)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()  # fmt: skip
            for conversation in due:
                await close_conversation(session, conversation, actor=None, automatic_after_days=days)
        total += len(due)
        if len(due) < batch_size:
            break
    if total:
        log.info("resolved_conversations_auto_closed", count=total, after_days=days)
    return total

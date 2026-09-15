"""Conversation, message, feedback and handoff queries. Every query is tenant-scoped."""

from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, Select, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models import Conversation, Feedback, Handoff, Message, RagTrace, User
from app.models.enums import HandoffStatus, MessageRole
from app.security.principal import Principal


def _like_pattern(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def search_clause(q: str) -> ColumnElement[bool]:
    """Title substring match, or a word match in any message (GIN full-text index)."""
    message_match = exists().where(
        Message.conversation_id == Conversation.id,
        func.to_tsvector("simple", Message.content).op("@@")(func.plainto_tsquery("simple", q)),
    )
    return or_(Conversation.title.ilike(_like_pattern(q), escape="\\"), message_match)


def has_messages_clause() -> ColumnElement[bool]:
    """Conversations with at least one message; empty shells are not shown in lists."""
    return exists().where(Message.conversation_id == Conversation.id)


async def first_user_messages(session: AsyncSession, conversation_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not conversation_ids:
        return {}
    rows = await session.execute(
        select(Message.conversation_id, Message.content)
        .where(Message.conversation_id.in_(conversation_ids), Message.role == MessageRole.USER)
        .order_by(Message.conversation_id, Message.created_at)
        .distinct(Message.conversation_id)
    )
    return {conversation_id: content for conversation_id, content in rows.tuples()}


async def get_owned(
    session: AsyncSession, principal: Principal, conversation_id: uuid.UUID, *, lock: bool = False
) -> Conversation:
    """The caller's own conversation. Other users' conversations are reported as not found."""
    stmt = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.company_id == principal.company_id,
        Conversation.user_id == principal.user_id,
    )
    conversation = (await session.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()
    if conversation is None:
        raise NotFoundError("Conversation not found.")
    return conversation


async def get_in_company(
    session: AsyncSession, company_id: uuid.UUID, conversation_id: uuid.UUID, *, lock: bool = False
) -> Conversation:
    stmt = select(Conversation).where(Conversation.id == conversation_id, Conversation.company_id == company_id)
    conversation = (await session.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()
    if conversation is None:
        raise NotFoundError("Conversation not found.")
    return conversation


async def paginate[T](session: AsyncSession, stmt: Select[tuple[T]], page: int, page_size: int) -> tuple[list[T], int]:
    total = await session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = (await session.execute(stmt.limit(page_size).offset((page - 1) * page_size))).scalars().all()
    return list(rows), total


async def messages_for(session: AsyncSession, conversation_id: uuid.UUID, limit: int = 500) -> list[Message]:
    rows = (
        (
            await session.execute(
                select(Message)
                .where(Message.conversation_id == conversation_id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return list(reversed(rows))


async def message_count(session: AsyncSession, conversation_id: uuid.UUID) -> int:
    return await session.scalar(select(func.count()).where(Message.conversation_id == conversation_id)) or 0


async def find_by_client_id(
    session: AsyncSession, conversation_id: uuid.UUID, client_message_id: str
) -> Message | None:
    return (
        await session.execute(
            select(Message).where(
                Message.conversation_id == conversation_id, Message.client_message_id == client_message_id
            )
        )
    ).scalar_one_or_none()


async def reply_to(session: AsyncSession, user_message_id: uuid.UUID) -> Message | None:
    return (
        await session.execute(select(Message).where(Message.reply_to_message_id == user_message_id).limit(1))
    ).scalar_one_or_none()


async def open_handoff(session: AsyncSession, conversation_id: uuid.UUID, *, lock: bool = False) -> Handoff | None:
    stmt = (
        select(Handoff)
        .where(
            Handoff.conversation_id == conversation_id,
            Handoff.status.in_([HandoffStatus.PENDING, HandoffStatus.ASSIGNED]),
        )
        .order_by(Handoff.created_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()


async def feedback_by_message(
    session: AsyncSession, user_id: uuid.UUID, message_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Feedback]:
    if not message_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(Feedback).where(Feedback.user_id == user_id, Feedback.message_id.in_(message_ids))
            )
        )
        .scalars()
        .all()
    )
    return {f.message_id: f for f in rows}


async def user_names(session: AsyncSession, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, User]:
    if not user_ids:
        return {}
    rows = (await session.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
    return {u.id: u for u in rows}


async def traces_for(session: AsyncSession, conversation_id: uuid.UUID) -> list[RagTrace]:
    rows = (
        (
            await session.execute(
                select(RagTrace).where(RagTrace.conversation_id == conversation_id).order_by(RagTrace.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)

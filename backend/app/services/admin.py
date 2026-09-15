"""Staff operations: conversation oversight, handoff queue, human replies, resolution."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from app.db.base import utcnow
from app.models import Conversation, Handoff, Message, User
from app.models.enums import ConversationStatus, HandoffStatus, MessageRole, UserRole
from app.repositories import conversations as repo
from app.schemas.admin import AdminConversationDetail, AdminConversationOut, HandoffOut, TraceOut, UserRef
from app.schemas.common import Page
from app.schemas.conversation import MessageOut, message_out
from app.security.principal import Principal
from app.services.audit import record_audit
from app.services.chat import _agent_display_name, _touch, actor_out
from app.services.lifecycle import close_conversation, mark_resolved
from app.utils.text import truncate


def _ref(user: User | None) -> UserRef | None:
    return UserRef(id=user.id, name=user.name, email=user.email, role=UserRole(user.role)) if user else None


class AdminService:
    async def conversations(
        self,
        session: AsyncSession,
        principal: Principal,
        page: int,
        page_size: int,
        q: str | None,
        status: ConversationStatus | None,
        handoff_status: HandoffStatus | None,
    ) -> Page[AdminConversationOut]:
        # One row per conversation; conversations without any message are empty shells and hidden.
        stmt = select(Conversation).where(Conversation.company_id == principal.company_id, repo.has_messages_clause())
        if q:
            stmt = stmt.where(repo.search_clause(q))
        if status:
            stmt = stmt.where(Conversation.status == status)
        if handoff_status:
            stmt = stmt.where(Conversation.handoff_status == handoff_status)
        items, total = await repo.paginate(session, stmt.order_by(Conversation.last_message_at.desc()), page, page_size)
        return Page.build(await self._conversation_outs(session, items), total, page, page_size)

    async def conversation_detail(
        self, session: AsyncSession, principal: Principal, conversation_id: uuid.UUID
    ) -> AdminConversationDetail:
        conversation = await repo.get_in_company(session, principal.company_id, conversation_id)
        messages = await repo.messages_for(session, conversation.id)
        users = await repo.user_names(session, {m.author_user_id for m in messages if m.author_user_id})
        handoffs = (
            await session.execute(select(Handoff).where(Handoff.conversation_id == conversation.id).order_by(Handoff.created_at.desc()))
        ).scalars().all()  # fmt: skip
        traces = await repo.traces_for(session, conversation.id)
        return AdminConversationDetail(
            conversation=(await self._conversation_outs(session, [conversation]))[0],
            messages=[self._message(m, users) for m in messages],
            handoffs=await self._handoff_outs(session, list(handoffs)),
            traces=[TraceOut.model_validate(t, from_attributes=True) for t in traces],
        )

    async def handoffs(
        self, session: AsyncSession, principal: Principal, page: int, page_size: int, status: HandoffStatus | None
    ) -> Page[HandoffOut]:
        stmt = select(Handoff).where(Handoff.company_id == principal.company_id)
        if status:
            stmt = stmt.where(Handoff.status == status)
        else:
            stmt = stmt.where(Handoff.status.in_([HandoffStatus.PENDING, HandoffStatus.ASSIGNED]))
        priority_order = func.array_position(["URGENT", "HIGH", "NORMAL", "LOW"], Handoff.priority)
        items, total = await repo.paginate(session, stmt.order_by(priority_order, Handoff.created_at), page, page_size)
        return Page.build(await self._handoff_outs(session, items), total, page, page_size)

    async def assign(
        self, session: AsyncSession, principal: Principal, handoff_id: uuid.UUID, agent_id: uuid.UUID | None
    ) -> HandoffOut:
        handoff = await self._handoff(session, principal, handoff_id)
        if handoff.status == HandoffStatus.RESOLVED:
            raise ConflictError("This handoff is already resolved.")
        target_id = agent_id or principal.user_id
        agent = await session.get(User, target_id)
        if (
            agent is None
            or agent.company_id != principal.company_id
            or agent.role not in (UserRole.ADMIN, UserRole.AGENT)
            or not agent.is_active
        ):
            raise ValidationAppError("Handoffs can only be assigned to active support staff.")
        if principal.role != UserRole.ADMIN and target_id != principal.user_id:
            raise ValidationAppError("Agents can only assign handoffs to themselves.")
        conversation = await repo.get_in_company(session, principal.company_id, handoff.conversation_id, lock=True)
        handoff.status = HandoffStatus.ASSIGNED
        handoff.assigned_agent_id = target_id
        handoff.assigned_at = utcnow()
        conversation.handoff_status = HandoffStatus.ASSIGNED
        conversation.assigned_agent_id = target_id
        record_audit(
            session,
            action="handoff.assigned",
            company_id=principal.company_id,
            actor_user_id=principal.user_id,
            target_type="handoff",
            target_id=handoff.id,
            details={"agent_id": str(target_id)},
        )
        await session.commit()
        return (await self._handoff_outs(session, [handoff]))[0]

    async def reply(
        self, session: AsyncSession, principal: Principal, conversation_id: uuid.UUID, content: str
    ) -> MessageOut:
        conversation = await repo.get_in_company(session, principal.company_id, conversation_id, lock=True)
        if conversation.status == ConversationStatus.CLOSED:
            raise ConflictError("This conversation is closed.")
        handoff = await repo.open_handoff(session, conversation.id, lock=True)
        if handoff is not None and handoff.status == HandoffStatus.PENDING:
            handoff.status = HandoffStatus.ASSIGNED
            handoff.assigned_agent_id = principal.user_id
            handoff.assigned_at = utcnow()
            conversation.handoff_status = HandoffStatus.ASSIGNED
            conversation.assigned_agent_id = principal.user_id
        message = Message(
            company_id=principal.company_id, conversation_id=conversation.id, role=MessageRole.HUMAN_AGENT,
            content=content.strip(), author_user_id=principal.user_id, citations=[], retrieval_metadata={"response_kind": "HUMAN"},
        )  # fmt: skip
        session.add(message)
        conversation.status = ConversationStatus.WAITING_FOR_CUSTOMER
        _touch(conversation, content)
        record_audit(
            session,
            action="conversation.agent_replied",
            company_id=principal.company_id,
            actor_user_id=principal.user_id,
            target_type="conversation",
            target_id=conversation.id,
        )
        await session.commit()
        return message_out(message, author_name=_agent_display_name(await session.get(User, principal.user_id)))

    async def resolve(
        self, session: AsyncSession, principal: Principal, conversation_id: uuid.UUID
    ) -> AdminConversationOut:
        conversation = await repo.get_in_company(session, principal.company_id, conversation_id, lock=True)
        await mark_resolved(session, conversation, principal)
        await session.commit()
        return (await self._conversation_outs(session, [conversation]))[0]

    async def close(
        self, session: AsyncSession, principal: Principal, conversation_id: uuid.UUID
    ) -> AdminConversationOut:
        conversation = await repo.get_in_company(session, principal.company_id, conversation_id, lock=True)
        await close_conversation(session, conversation, actor=principal)
        await session.commit()
        return (await self._conversation_outs(session, [conversation]))[0]

    async def delete_conversation(
        self, session: AsyncSession, principal: Principal, conversation_id: uuid.UUID
    ) -> None:
        conversation = await repo.get_in_company(session, principal.company_id, conversation_id, lock=True)
        record_audit(
            session,
            action="conversation.deleted",
            company_id=principal.company_id,
            actor_user_id=principal.user_id,
            target_type="conversation",
            target_id=conversation.id,
        )
        await session.delete(conversation)
        await session.commit()

    # --- mapping ------------------------------------------------------------------------

    @staticmethod
    def _message(message: Message, users: dict[uuid.UUID, User]) -> MessageOut:
        author = users.get(message.author_user_id) if message.author_user_id else None
        name = author.name if author and message.role == MessageRole.HUMAN_AGENT else None
        return message_out(message, author_name=name)

    async def _handoff(self, session: AsyncSession, principal: Principal, handoff_id: uuid.UUID) -> Handoff:
        handoff = (
            await session.execute(select(Handoff).where(Handoff.id == handoff_id, Handoff.company_id == principal.company_id).with_for_update())
        ).scalar_one_or_none()  # fmt: skip
        if handoff is None:
            raise NotFoundError("Handoff not found.")
        return handoff

    async def _conversation_outs(
        self, session: AsyncSession, conversations: list[Conversation]
    ) -> list[AdminConversationOut]:
        user_ids = (
            {c.user_id for c in conversations}
            | {c.assigned_agent_id for c in conversations if c.assigned_agent_id}
            | {c.closed_by_user_id for c in conversations if c.closed_by_user_id}
            | {c.resolved_by_user_id for c in conversations if c.resolved_by_user_id}
        )
        users = await repo.user_names(session, user_ids)
        counts: dict[uuid.UUID, int] = {}
        if conversations:
            rows = await session.execute(
                select(Message.conversation_id, func.count())
                .where(Message.conversation_id.in_([c.id for c in conversations]))
                .group_by(Message.conversation_id)
            )
            counts = {conversation_id: count for conversation_id, count in rows.tuples()}
        outs = []
        for c in conversations:
            customer = _ref(users.get(c.user_id))
            assert customer is not None
            outs.append(AdminConversationOut(
                id=c.id, title=c.title, status=c.status, handoff_status=c.handoff_status,
                last_message_preview=c.last_message_preview, last_message_at=c.last_message_at, created_at=c.created_at,
                updated_at=c.updated_at, closed_at=c.closed_at, customer=customer,
                assigned_agent=_ref(users.get(c.assigned_agent_id)) if c.assigned_agent_id else None,
                message_count=counts.get(c.id, 0),
                closed_by=actor_out(users.get(c.closed_by_user_id), full_name=True) if c.closed_by_user_id else None,
                closed_automatically=c.closed_automatically, resolved_at=c.resolved_at,
                resolved_by=actor_out(users.get(c.resolved_by_user_id), full_name=True) if c.resolved_by_user_id else None,
                reopened_at=c.reopened_at, reopen_count=c.reopen_count,
            ))  # fmt: skip
        return outs

    async def _handoff_outs(self, session: AsyncSession, handoffs: list[Handoff]) -> list[HandoffOut]:
        if not handoffs:
            return []
        conversations = {
            c.id: c
            for c in (
                await session.execute(
                    select(Conversation).where(Conversation.id.in_({h.conversation_id for h in handoffs}))
                )
            ).scalars()
        }
        users = await repo.user_names(
            session,
            {c.user_id for c in conversations.values()}
            | {h.assigned_agent_id for h in handoffs if h.assigned_agent_id},
        )
        triggers = {
            m.id: m for m in (await session.execute(
                select(Message).where(Message.id.in_({h.triggering_message_id for h in handoffs if h.triggering_message_id}))
            )).scalars()
        }  # fmt: skip
        now = datetime.now(UTC)
        outs = []
        for h in handoffs:
            conversation = conversations[h.conversation_id]
            end = h.resolved_at or now
            trigger = triggers.get(h.triggering_message_id) if h.triggering_message_id else None
            outs.append(HandoffOut(
                id=h.id, conversation_id=h.conversation_id, conversation_title=conversation.title,
                customer=_ref(users[conversation.user_id]),
                reason_code=h.reason_code, reason_detail=h.reason_detail, priority=h.priority, status=h.status,
                assigned_agent=_ref(users.get(h.assigned_agent_id)) if h.assigned_agent_id else None,
                triggering_message_id=h.triggering_message_id,
                triggering_message_preview=truncate(trigger.content, 200) if trigger else None,
                created_at=h.created_at, assigned_at=h.assigned_at, resolved_at=h.resolved_at,
                waiting_seconds=max(0, int((end - h.created_at).total_seconds())),
            ))  # fmt: skip
        return outs

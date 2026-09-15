"""Resolve -> reopen -> automatic close, and no duplicate conversations for the same chat."""

from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select, update

from app.core.container import Container
from app.db.base import utcnow
from app.models import AuditLog, Conversation
from app.services.lifecycle import auto_close_resolved
from tests.conftest import make_settings
from tests.integration.conftest import Tenant, bearer

pytestmark = pytest.mark.integration


async def _start(client: httpx.AsyncClient, headers: dict, first_message: str | None = None) -> httpx.Response:
    return await client.post("/api/conversations", headers=headers, json={"first_message": first_message})


async def _send(client: httpx.AsyncClient, headers: dict, conversation_id: str, text: str) -> dict:
    response = await client.post(
        f"/api/conversations/{conversation_id}/messages", headers=headers, json={"content": text}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_resolve_then_customer_reply_reopens_the_same_conversation(client: httpx.AsyncClient, acme: Tenant):
    customer, agent = await bearer(client, acme.customer), await bearer(client, acme.agent)
    conversation_id = (await _start(client, customer, "Hello there")).json()["id"]
    await _send(client, customer, conversation_id, "Hello there")

    resolved = await client.post(f"/api/admin/conversations/{conversation_id}/resolve", headers=agent)
    body = resolved.json()
    assert resolved.status_code == 200 and body["status"] == "RESOLVED" and body["resolved_at"]
    assert body["resolved_by"] == {"id": str(acme.agent.id), "name": acme.agent.name, "role": "AGENT"}
    again = await client.post(f"/api/admin/conversations/{conversation_id}/resolve", headers=agent)
    assert again.status_code == 200

    customer_view = (await client.get(f"/api/conversations/{conversation_id}", headers=customer)).json()
    support_name = f"{acme.agent.name.split()[0]} (Support)"
    events = [m["content"] for m in customer_view["messages"] if m["role"] == "SYSTEM"]
    assert events == [f"Marked resolved by {support_name}."]  # resolving twice adds one event
    assert customer_view["conversation"]["resolved_by"]["name"] == support_name

    await _send(client, customer, conversation_id, "Actually, one more question")
    reopened = (await client.get(f"/api/conversations/{conversation_id}", headers=customer)).json()
    conversation = reopened["conversation"]
    assert conversation["id"] == conversation_id and conversation["status"] == "OPEN"
    assert conversation["reopen_count"] == 1 and conversation["reopened_at"]
    roles_and_text = [(m["role"], m["content"]) for m in reopened["messages"]]
    reopen_index = roles_and_text.index(("SYSTEM", f"Reopened by {acme.customer.name}."))
    assert roles_and_text[reopen_index + 1] == ("USER", "Actually, one more question")

    staff_list = (await client.get("/api/admin/conversations", headers=agent)).json()
    assert staff_list["total"] == 1
    row = staff_list["items"][0]
    assert row["id"] == conversation_id and row["reopen_count"] == 1 and row["resolved_by"]["name"] == acme.agent.name


async def test_same_opening_question_continues_the_existing_chat(client: httpx.AsyncClient, acme: Tenant):
    customer, agent = await bearer(client, acme.customer), await bearer(client, acme.agent)
    first = await _start(client, customer, "How do I reset my password?")
    assert first.status_code == 201 and first.json()["reused"] is False
    conversation_id = first.json()["id"]
    await _send(client, customer, conversation_id, "How do I reset my password?")

    # Same question again (e.g. clicking the same suggestion): the existing chat is returned.
    repeat = await _start(client, customer, "  how do I reset my password  ")
    assert repeat.status_code == 200 and repeat.json()["reused"] is True and repeat.json()["id"] == conversation_id

    # Also while it is resolved: the customer's message will reopen it rather than start a duplicate.
    await client.post(f"/api/admin/conversations/{conversation_id}/resolve", headers=agent)
    assert (await _start(client, customer, "How do I reset my password?")).json()["id"] == conversation_id

    # A different question is a different chat.
    other = await _start(client, customer, "What are your support hours?")
    assert other.status_code == 201 and other.json()["id"] != conversation_id
    await _send(client, customer, other.json()["id"], "What are your support hours?")

    # A closed conversation is never continued.
    await client.post(f"/api/conversations/{conversation_id}/close", headers=customer)
    fresh = await _start(client, customer, "How do I reset my password?")
    assert fresh.status_code == 201 and fresh.json()["id"] != conversation_id


async def test_conversations_without_messages_are_not_listed(client: httpx.AsyncClient, acme: Tenant):
    customer, agent = await bearer(client, acme.customer), await bearer(client, acme.agent)
    empty = (await _start(client, customer)).json()["id"]
    used = (await _start(client, customer, "What are your support hours?")).json()["id"]
    assert used == empty  # an empty conversation is reused rather than left behind
    assert (await client.get("/api/admin/conversations", headers=agent)).json()["total"] == 0
    assert (await client.get("/api/conversations", headers=customer)).json()["total"] == 0
    await _send(client, customer, used, "What are your support hours?")
    assert (await client.get("/api/admin/conversations", headers=agent)).json()["total"] == 1


async def test_resolved_conversation_without_reply_closes_automatically(
    client: httpx.AsyncClient, acme: Tenant, container: Container, tmp_path
):
    customer, agent = await bearer(client, acme.customer), await bearer(client, acme.agent)
    ids = []
    for text in ("Stale question", "Recent question"):
        conversation_id = (await _start(client, customer, text)).json()["id"]
        await _send(client, customer, conversation_id, text)
        await client.post(f"/api/admin/conversations/{conversation_id}/resolve", headers=agent)
        ids.append(conversation_id)
    stale, recent = ids
    async with container.sessions() as session, session.begin():
        await session.execute(
            update(Conversation).where(Conversation.id == stale).values(resolved_at=utcnow() - timedelta(days=8))
        )

    disabled = make_settings(tmp_path, database_url=container.settings.database_url, resolved_auto_close_days=0)
    assert await auto_close_resolved(container.sessions, disabled) == 0
    assert await auto_close_resolved(container.sessions, container.settings) == 1
    assert await auto_close_resolved(container.sessions, container.settings) == 0  # idempotent

    closed = (await client.get(f"/api/conversations/{stale}", headers=customer)).json()
    assert closed["conversation"]["status"] == "CLOSED" and closed["conversation"]["closed_automatically"] is True
    assert closed["conversation"]["closed_by"] is None and closed["conversation"]["closed_at"]
    assert closed["messages"][-1]["content"] == "Conversation closed automatically after 7 days without a reply."
    still_resolved = (await client.get(f"/api/conversations/{recent}", headers=customer)).json()["conversation"]
    assert still_resolved["status"] == "RESOLVED"

    async with container.sessions() as session:
        actions = (await session.execute(select(AuditLog.action).where(AuditLog.target_id == stale))).scalars().all()
    assert "conversation.auto_closed" in actions

    rejected = await client.post(f"/api/conversations/{stale}/messages", headers=customer, json={"content": "Hello?"})
    assert rejected.status_code == 409

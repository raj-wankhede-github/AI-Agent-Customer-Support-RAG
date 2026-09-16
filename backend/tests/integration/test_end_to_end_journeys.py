"""End-to-end journeys through the product, exercising both apps' APIs in order.

These tests follow what a real customer and a real support agent do, from an empty knowledge
base to a closed conversation, and assert what each side sees at every step - including the
transcript events, attribution and audit trail the UI renders.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.core.container import Container
from app.models import AuditLog, User
from app.models.enums import UserRole
from app.services.auth import create_user
from tests.integration.conftest import PASSWORD, Tenant, bearer, ingest

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
SEED = BACKEND / "seed" / "acme"


async def ask(client: httpx.AsyncClient, headers: dict[str, str], conversation_id: str, content: str) -> dict:
    response = await client.post(
        f"/api/conversations/{conversation_id}/messages", headers=headers, json={"content": content}
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def transcript(client: httpx.AsyncClient, headers: dict[str, str], conversation_id: str) -> dict:
    response = await client.get(f"/api/conversations/{conversation_id}", headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


def events(detail: dict) -> list[str]:
    return [m["content"] for m in detail["messages"] if m["role"] == "SYSTEM"]


async def test_customer_journey_from_question_to_close(client: httpx.AsyncClient, acme: Tenant, container: Container):
    """Upload -> answer with citation -> feedback -> abstain -> handoff -> agent reply ->
    resolve -> customer reply reopens -> close, checked from both sides."""
    admin, agent, customer = (
        await bearer(client, acme.admin),
        await bearer(client, acme.agent),
        await bearer(client, acme.customer),
    )

    # 1. The admin publishes the shipping policy and the worker indexes it.
    upload = await client.post(
        "/api/knowledge/documents",
        headers=admin,
        files={"file": ("shipping-policy.pdf", (SEED / "shipping-policy.pdf").read_bytes())},
        data={"title": "Shipping Policy", "authority": "OFFICIAL_POLICY"},
    )
    assert upload.status_code == 202
    assert await ingest(container) == 1
    document = (await client.get(f"/api/knowledge/documents/{upload.json()['document']['id']}", headers=admin)).json()
    assert document["document"]["searchable"] is True

    # 2. The customer asks a covered question and gets a cited answer.
    start = await client.post(
        "/api/conversations", headers=customer, json={"first_message": "What is your standard shipping time?"}
    )
    assert start.status_code == 201 and start.json()["reused"] is False
    conversation_id = start.json()["id"]
    answered = await ask(client, customer, conversation_id, "What is your standard shipping time?")
    assert answered["answer_status"] == "ANSWERED" and answered["citations"]
    assert answered["citations"][0]["document_title"] == "Shipping Policy"

    # 3. Feedback on that answer is stored and visible on reload.
    feedback = await client.post(
        f"/api/conversations/{conversation_id}/feedback",
        headers=customer,
        json={"message_id": answered["message_id"], "rating": "HELPFUL"},
    )
    assert feedback.status_code == 204
    detail = await transcript(client, customer, conversation_id)
    rated = next(m for m in detail["messages"] if m["id"] == answered["message_id"])
    assert rated["feedback"]["rating"] == "HELPFUL"

    # 4. An unsupported question abstains instead of guessing.
    abstained = await ask(client, customer, conversation_id, "Who is your chief executive's favourite author?")
    assert abstained["answer_status"] == "ABSTAINED" and not abstained["citations"]

    # 5. The customer asks for a person; the conversation waits for support.
    handoff = await client.post(
        f"/api/conversations/{conversation_id}/handoff", headers=customer, json={"note": "I need a human"}
    )
    assert handoff.status_code == 200 and handoff.json()["conversation_status"] == "WAITING_FOR_HUMAN"

    # 6. The agent finds it in the queue, assigns it and replies.
    queue = (await client.get("/api/admin/handoffs", headers=agent, params={"status": "PENDING"})).json()
    queued = next(h for h in queue["items"] if h["conversation_id"] == conversation_id)
    assert queued["reason_code"] == "USER_REQUESTED"
    assigned = await client.post(f"/api/admin/handoffs/{queued['id']}/assign", headers=agent, json={})
    assert assigned.status_code == 200
    reply = await client.post(
        f"/api/admin/conversations/{conversation_id}/messages",
        headers=agent,
        json={"content": "Hello, this is Acme Agent. Standard shipping takes 3-5 business days."},
    )
    assert reply.status_code == 201

    # 7. The customer sees the human reply, attributed and awaiting their answer.
    detail = await transcript(client, customer, conversation_id)
    human = [m for m in detail["messages"] if m["role"] == "HUMAN_AGENT"]
    assert human and human[-1]["author_name"] == "Acme (Support)"  # staff keep their surname private
    assert detail["conversation"]["status"] == "WAITING_FOR_CUSTOMER"

    # 8. The agent marks it resolved: recorded, attributed and shown in the transcript.
    resolved = await client.post(f"/api/admin/conversations/{conversation_id}/resolve", headers=agent)
    assert resolved.status_code == 200
    detail = await transcript(client, customer, conversation_id)
    assert detail["conversation"]["status"] == "RESOLVED"
    assert detail["conversation"]["resolved_by"]["name"] == "Acme (Support)"
    assert detail["conversation"]["resolved_at"]
    assert any(e.startswith("Marked resolved by") for e in events(detail))

    # 9. A later customer reply reopens the same conversation instead of starting a new one.
    reopened = await ask(client, customer, conversation_id, "What about international shipping?")
    assert reopened["conversation_status"] in ("OPEN", "WAITING_FOR_HUMAN")
    detail = await transcript(client, customer, conversation_id)
    assert detail["conversation"]["reopen_count"] == 1 and detail["conversation"]["reopened_at"]
    assert any(e.startswith("Reopened by") for e in events(detail))
    staff_list = (await client.get("/api/admin/conversations", headers=agent)).json()
    assert [row["id"] for row in staff_list["items"]].count(conversation_id) == 1

    # 10. The agent closes it; it stays readable but accepts nothing further.
    closed = await client.post(f"/api/admin/conversations/{conversation_id}/close", headers=agent)
    assert closed.status_code == 200
    detail = await transcript(client, customer, conversation_id)
    assert detail["conversation"]["status"] == "CLOSED"
    assert detail["conversation"]["closed_by"]["name"] == "Acme (Support)"
    assert detail["conversation"]["closed_automatically"] is False
    assert any(e.startswith("Conversation closed by") for e in events(detail))
    rejected = await client.post(
        f"/api/conversations/{conversation_id}/messages", headers=customer, json={"content": "One more thing"}
    )
    assert rejected.status_code == 409

    # 11. The security-relevant steps are in the audit log.
    async with container.sessions() as session:
        actions = set(
            (await session.execute(select(AuditLog.action).where(AuditLog.company_id == acme.company.id)))
            .scalars()
            .all()
        )
    assert {"document.uploaded", "conversation.resolved", "conversation.reopened", "conversation.closed"} <= actions


async def test_support_queue_and_metrics_across_customers(
    client: httpx.AsyncClient, acme: Tenant, container: Container
):
    """Two customers, one queue: staff trigage each conversation and the dashboard counts it."""
    admin, agent = await bearer(client, acme.admin), await bearer(client, acme.agent)
    async with container.sessions() as session:
        second = await create_user(
            session,
            company_id=acme.company.id,
            email="second.customer@acme.test",
            name="Second Customer",
            role=UserRole.CUSTOMER,
            password=PASSWORD,
        )
        await session.commit()

    upload = await client.post(
        "/api/knowledge/documents",
        headers=admin,
        files={"file": ("shipping-policy.pdf", (SEED / "shipping-policy.pdf").read_bytes())},
        data={"title": "Shipping Policy", "authority": "OFFICIAL_POLICY"},
    )
    assert upload.status_code == 202
    await ingest(container)

    conversations: dict[str, str] = {}
    for user in (acme.customer, second):
        headers = await bearer(client, user)
        start = await client.post("/api/conversations", headers=headers, json={"first_message": "I need help"})
        conversation_id = start.json()["id"]
        await ask(client, headers, conversation_id, "What is your standard shipping time?")
        await client.post(f"/api/conversations/{conversation_id}/handoff", headers=headers, json={})
        conversations[user.email] = conversation_id

    pending = (await client.get("/api/admin/handoffs", headers=agent, params={"status": "PENDING"})).json()
    assert pending["total"] == 2
    first_handoff = pending["items"][0]
    assert (
        await client.post(f"/api/admin/handoffs/{first_handoff['id']}/assign", headers=agent, json={})
    ).status_code == 200
    assert (await client.get("/api/admin/handoffs", headers=agent, params={"status": "PENDING"})).json()["total"] == 1
    assigned = (await client.get("/api/admin/handoffs", headers=agent, params={"status": "ASSIGNED"})).json()
    assert assigned["total"] == 1 and assigned["items"][0]["assigned_agent"]["name"] == "Acme Agent"

    staff_list = (await client.get("/api/admin/conversations", headers=agent)).json()
    assert staff_list["total"] == 2
    assert all(row["status"] == "WAITING_FOR_HUMAN" for row in staff_list["items"])
    searched = (await client.get("/api/admin/conversations", headers=agent, params={"q": "shipping"})).json()
    assert searched["total"] == 2

    metrics = (await client.get("/api/admin/metrics", headers=agent, params={"days": 30})).json()
    assert metrics["conversations_total"] == 2
    assert metrics["conversations_waiting_for_human"] == 2
    assert metrics["handoff_rate"] == 1.0
    assert metrics["pending_handoffs"] == 2  # still open: one pending, one assigned
    assert metrics["documents_searchable"] == 1
    assert any(item["key"] == "USER_REQUESTED" and item["count"] == 2 for item in metrics["handoff_reasons"])

    # Each customer still sees only their own conversation.
    for user, conversation_id in (
        (acme.customer, conversations[acme.customer.email]),
        (second, conversations[second.email]),
    ):
        headers = await bearer(client, user)
        mine = (await client.get("/api/conversations", headers=headers)).json()
        assert [row["id"] for row in mine["items"]] == [conversation_id]


async def test_second_version_replaces_the_answer_and_deactivation_removes_it(
    client: httpx.AsyncClient, acme: Tenant, container: Container
):
    """A corrected document changes the answer only once it is ready, and deactivating it
    makes the assistant abstain rather than serve stale content."""
    admin, customer = await bearer(client, acme.admin), await bearer(client, acme.customer)
    original = b"# Support hours\n\nOur support team is available 9am to 5pm Monday to Friday.\n"
    corrected = b"# Support hours\n\nOur support team is available 7am to 11pm Monday to Friday.\n"

    created = await client.post(
        "/api/knowledge/documents",
        headers=admin,
        files={"file": ("support-hours.md", original)},
        data={"title": "Support hours", "authority": "OFFICIAL_POLICY"},
    )
    assert created.status_code == 202
    document_id = created.json()["document"]["id"]
    await ingest(container)

    conversation_id = (await client.post("/api/conversations", headers=customer, json={})).json()["id"]
    first = await ask(client, customer, conversation_id, "What are your support hours?")
    assert first["answer_status"] == "ANSWERED" and "9am to 5pm" in first["answer"]

    # A new version is queued but not yet processed: the current version keeps answering.
    new_version = await client.post(
        "/api/knowledge/documents",
        headers=admin,
        files={"file": ("support-hours.md", corrected)},
        data={"document_id": document_id},
    )
    assert new_version.status_code == 202
    during = await ask(client, customer, conversation_id, "What are your support hours?")
    assert during["answer_status"] == "ANSWERED" and "9am to 5pm" in during["answer"]

    await ingest(container)
    after = await ask(client, customer, conversation_id, "What are your support hours?")
    assert after["answer_status"] == "ANSWERED" and "7am to 11pm" in after["answer"]

    detail = (await client.get(f"/api/knowledge/documents/{document_id}", headers=admin)).json()
    statuses = sorted(version["status"] for version in detail["versions"])
    assert statuses == ["READY", "SUPERSEDED"]

    deactivated = await client.patch(
        f"/api/knowledge/documents/{document_id}", headers=admin, json={"status": "INACTIVE"}
    )
    assert deactivated.status_code == 200
    silent = await ask(client, customer, conversation_id, "What are your support hours?")
    assert silent["answer_status"] != "ANSWERED" and not silent["citations"]

    reactivated = await client.patch(
        f"/api/knowledge/documents/{document_id}", headers=admin, json={"status": "ACTIVE"}
    )
    assert reactivated.status_code == 200
    restored = await ask(client, customer, conversation_id, "What are your support hours?")
    assert restored["answer_status"] == "ANSWERED" and "7am to 11pm" in restored["answer"]


async def test_users_deactivated_mid_session_lose_access(client: httpx.AsyncClient, acme: Tenant, container: Container):
    """A valid token stops working as soon as the account is disabled."""
    customer = await bearer(client, acme.customer)
    assert (await client.get("/api/conversations", headers=customer)).status_code == 200

    async with container.sessions() as session:
        user = await session.get(User, acme.customer.id)
        assert user is not None
        user.is_active = False
        await session.commit()

    blocked = await client.get("/api/conversations", headers=customer)
    assert blocked.status_code == 401 and blocked.json()["error"]["code"] == "AUTHENTICATION_ERROR"
    login = await client.post("/api/auth/login", json={"email": acme.customer.email, "password": PASSWORD})
    assert login.status_code == 401

"""HTTP-level tests: auth, authorization, errors, and the end-to-end acceptance workflows.

The application runs with its real services, PostgreSQL + pgvector, the offline hashing
embedder and extractive generator (or a scripted fake LLM where noted). No paid APIs.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from app.core.container import Container, build_container
from app.core.errors import LLMError
from app.main import create_app
from app.models import AuditLog
from tests.helpers import ScriptedLLMProvider
from tests.integration.conftest import PASSWORD, Tenant, bearer, create_tenant, ingest

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[2]
SEED = BACKEND / "seed" / "acme"
SAMPLES = BACKEND.parent / "samples"


async def upload(client: httpx.AsyncClient, headers: dict, path: Path, **form: str) -> dict:
    response = await client.post(
        "/api/knowledge/documents", headers=headers, files={"file": (path.name, path.read_bytes())}, data=form
    )
    assert response.status_code == 202, response.text
    return response.json()


async def ask(client: httpx.AsyncClient, headers: dict, conversation_id: str, content: str, **extra) -> dict:
    response = await client.post(
        f"/api/conversations/{conversation_id}/messages", headers=headers, json={"content": content, **extra}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def new_conversation(client: httpx.AsyncClient, headers: dict) -> str:
    response = await client.post("/api/conversations", headers=headers, json={})
    assert response.status_code == 201, response.text
    return response.json()["id"]


# --- platform --------------------------------------------------------------------------


async def test_health_and_readiness(client: httpx.AsyncClient):
    assert (await client.get("/health")).json() == {"status": "ok"}
    ready = await client.get("/ready")
    assert ready.status_code == 200 and ready.json()["checks"] == {"database": True, "storage": True}
    assert ready.headers["x-content-type-options"] == "nosniff" and ready.headers["x-request-id"]


async def test_login_me_and_logout_revokes_the_token(client: httpx.AsyncClient, acme: Tenant, container: Container):
    headers = await bearer(client, acme.customer)
    me = await client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200 and me.json()["role"] == "CUSTOMER" and me.json()["company_name"] == "Acme Inc"
    assert (await client.post("/api/auth/logout", headers=headers)).status_code == 204
    revoked = await client.get("/api/auth/me", headers=headers)
    assert revoked.status_code == 401 and revoked.json()["error"]["code"] == "AUTHENTICATION_ERROR"
    async with container.sessions() as session:
        actions = set((await session.execute(select(AuditLog.action))).scalars())
    assert {"auth.login", "auth.logout"} <= actions


async def test_invalid_credentials_get_a_generic_error(client: httpx.AsyncClient, acme: Tenant):
    wrong_password = await client.post("/api/auth/login", json={"email": acme.admin.email, "password": "nope"})
    unknown_user = await client.post("/api/auth/login", json={"email": "ghost@acme.test", "password": "nope"})
    assert wrong_password.status_code == unknown_user.status_code == 401
    assert (
        wrong_password.json()["error"]["message"]
        == unknown_user.json()["error"]["message"]
        == "Invalid email or password."
    )


async def test_cookie_sessions_require_the_csrf_header_for_writes(client: httpx.AsyncClient, acme: Tenant):
    login = await client.post("/api/auth/login", json={"email": acme.customer.email, "password": PASSWORD})
    assert "httponly" in login.headers["set-cookie"].lower() and "samesite=lax" in login.headers["set-cookie"].lower()
    assert (await client.get("/api/auth/me")).status_code == 200  # cookie auth for reads
    blocked = await client.post("/api/conversations", json={})
    assert blocked.status_code == 403
    allowed = await client.post("/api/conversations", json={}, headers={"X-Requested-With": "XMLHttpRequest"})
    assert allowed.status_code == 201


async def test_role_based_access_control(client: httpx.AsyncClient, acme: Tenant):
    customer = await bearer(client, acme.customer)
    agent = await bearer(client, acme.agent)
    admin = await bearer(client, acme.admin)
    file = {"file": ("faq.md", b"# FAQ\n\nText")}
    assert (await client.post("/api/knowledge/documents", headers=customer, files=file)).status_code == 403
    assert (await client.post("/api/knowledge/documents", headers=agent, files=file)).status_code == 403
    assert (await client.get("/api/knowledge/documents", headers=agent)).status_code == 403
    assert (await client.get("/api/admin/handoffs", headers=customer)).status_code == 403
    assert (await client.get("/api/admin/handoffs", headers=agent)).status_code == 200
    assert (await client.post("/api/admin/retrieval/debug", headers=agent, json={"query": "x"})).status_code == 403
    assert (await client.post("/api/knowledge/documents", headers=admin, files=file)).status_code == 202


async def test_customers_cannot_read_other_customers_or_tenants_conversations(
    client: httpx.AsyncClient, acme: Tenant, container: Container
):
    globex = await create_tenant(container, "globex")
    owner = await bearer(client, acme.customer)
    conversation_id = await new_conversation(client, owner)
    for intruder in (acme.agent, globex.customer):
        # An agent may view it via the admin API, but not through the customer endpoints.
        headers = await bearer(client, intruder)
        assert (await client.get(f"/api/conversations/{conversation_id}", headers=headers)).status_code == 404
    globex_agent = await bearer(client, globex.agent)
    assert (await client.get(f"/api/admin/conversations/{conversation_id}", headers=globex_agent)).status_code == 404


async def test_errors_are_classified_and_safe(client: httpx.AsyncClient, acme: Tenant):
    headers = await bearer(client, acme.admin)
    invalid = await client.post("/api/conversations/not-a-uuid/messages", headers=headers, json={})
    body = invalid.json()["error"]
    assert invalid.status_code == 422 and body["code"] == "VALIDATION_ERROR" and body["fields"] and body["request_id"]
    exe = await client.post("/api/knowledge/documents", headers=headers, files={"file": ("tool.exe", b"MZ")})
    assert exe.status_code == 415 and exe.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"
    missing = await client.get("/api/knowledge/documents/00000000-0000-0000-0000-000000000000", headers=headers)
    assert missing.status_code == 404 and "Traceback" not in missing.text


# --- acceptance workflows -------------------------------------------------------------------


async def test_acceptance_end_to_end_support_workflow(client: httpx.AsyncClient, acme: Tenant, container: Container):
    admin = await bearer(client, acme.admin)
    customer = await bearer(client, acme.customer)
    agent = await bearer(client, acme.agent)

    # 1-4. Admin uploads the shipping policy PDF and it becomes READY.
    uploaded = await upload(
        client, admin, SEED / "shipping-policy.pdf", authority="OFFICIAL_POLICY", category="shipping"
    )
    assert uploaded["version"]["status"] == "UPLOADED" and uploaded["document"]["searchable"] is False
    await ingest(container)
    detail = (await client.get(f"/api/knowledge/documents/{uploaded['document']['id']}", headers=admin)).json()
    assert detail["document"]["processing_status"] == "READY" and detail["document"]["searchable"] and detail["chunks"]

    # 5-9. Customer asks an answerable question and gets a grounded, cited answer.
    conversation_id = await new_conversation(client, customer)
    answer = await ask(client, customer, conversation_id, "What is your standard shipping time?")
    assert answer["answer_status"] == "ANSWERED" and "3-5 business days" in answer["answer"]
    citation = answer["citations"][0]
    assert (
        citation["document_title"] == "Shipping Policy"
        and citation["page_number"] == 1
        and "3-5" in citation["excerpt"]
    )

    # 10. Unanswerable question: abstain, no invented answer.
    abstain = await ask(client, customer, conversation_id, "What is your CEO's favorite food?")
    assert abstain["answer_status"] == "ABSTAINED" and abstain["citations"] == [] and not abstain["handoff_required"]

    # 11. Ask for a human: handoff is created.
    handoff = await ask(client, customer, conversation_id, "Connect me to a human.")
    assert handoff["answer_status"] == "HANDOFF_REQUIRED" and handoff["handoff_reason"] == "USER_REQUESTED"
    assert handoff["conversation_status"] == "WAITING_FOR_HUMAN"
    waiting = await ask(client, customer, conversation_id, "My order number is 4471 if that helps.")
    assert waiting["answer_status"] == "AWAITING_HUMAN" and waiting["message_id"] is None  # the AI stays silent

    # 12-13. The conversation is in the handoff queue with full history and RAG traces.
    queue = (await client.get("/api/admin/handoffs", headers=agent)).json()
    entry = next(h for h in queue["items"] if h["conversation_id"] == conversation_id)
    assert (
        entry["reason_code"] == "USER_REQUESTED"
        and entry["status"] == "PENDING"
        and entry["customer"]["email"] == acme.customer.email
    )
    staff_view = (await client.get(f"/api/admin/conversations/{conversation_id}", headers=agent)).json()
    assert [m["role"] for m in staff_view["messages"]] == ["USER", "ASSISTANT"] * 3 + ["USER"]
    assert len(staff_view["traces"]) == 3 and staff_view["traces"][0]["selected_evidence"]

    # 14. The human agent replies; the customer sees it.
    reply = await client.post(f"/api/admin/conversations/{conversation_id}/messages", headers=agent,
                              json={"content": "Hi Casey, I'm checking order 4471 for you now."})  # fmt: skip
    assert reply.status_code == 201
    customer_view = (await client.get(f"/api/conversations/{conversation_id}", headers=customer)).json()
    assert customer_view["messages"][-1]["role"] == "HUMAN_AGENT" and "4471" in customer_view["messages"][-1]["content"]
    assert customer_view["conversation"]["handoff_status"] == "ASSIGNED"

    # Feedback is persisted on the grounded answer.
    feedback = await client.post(f"/api/conversations/{conversation_id}/feedback", headers=customer,
                                 json={"message_id": answer["message_id"], "rating": "HELPFUL"})  # fmt: skip
    assert feedback.status_code == 204

    # 15. Resolve, close; the conversation stays in history and accepts no new messages.
    assert (await client.post(f"/api/admin/conversations/{conversation_id}/resolve", headers=agent)).status_code == 200
    closed = await client.post(f"/api/conversations/{conversation_id}/close", headers=customer)
    assert closed.status_code == 200 and closed.json()["status"] == "CLOSED"
    history = (await client.get("/api/conversations", headers=customer)).json()
    assert any(c["id"] == conversation_id and c["status"] == "CLOSED" for c in history["items"])
    rejected = await client.post(
        f"/api/conversations/{conversation_id}/messages", headers=customer, json={"content": "Hello?"}
    )
    assert rejected.status_code == 409

    metrics = (await client.get("/api/admin/metrics", headers=agent)).json()
    assert metrics["conversations_total"] == 1 and metrics["answered"] == 1 and metrics["abstained"] == 1
    assert metrics["feedback_helpful"] == 1 and {"key": "USER_REQUESTED", "count": 1} in metrics["handoff_reasons"]
    search = (await client.get("/api/conversations", headers=customer, params={"q": "shipping"})).json()
    assert search["total"] == 1


async def test_acceptance_multi_turn_uses_conversation_context(
    client: httpx.AsyncClient, acme: Tenant, container: Container
):
    admin, customer = await bearer(client, acme.admin), await bearer(client, acme.customer)
    await upload(client, admin, SEED / "refund-policy.md", authority="OFFICIAL_POLICY")
    await ingest(container)
    conversation_id = await new_conversation(client, customer)
    first = await ask(client, customer, conversation_id, "What is your refund policy?")
    assert first["answer_status"] == "ANSWERED"
    follow_up = await ask(client, customer, conversation_id, "What about international purchases?")
    assert follow_up["answer_status"] == "ANSWERED" and "45 days" in follow_up["answer"]
    assert {c["section_title"] for c in follow_up["citations"]} == {"International Purchases"}


async def test_acceptance_contradictions_resolved_by_authority_or_escalated(
    client: httpx.AsyncClient, acme: Tenant, container: Container
):
    admin, customer = await bearer(client, acme.admin), await bearer(client, acme.customer)
    faq = await upload(client, admin, SAMPLES / "contradiction" / "returns-faq-2024.md", authority="FAQ")
    await upload(client, admin, SAMPLES / "contradiction" / "returns-policy-2026.md", authority="OFFICIAL_POLICY")
    await ingest(container)
    conversation_id = await new_conversation(client, customer)
    resolved = await ask(client, customer, conversation_id, "How many days do I have to return a Gadget Pro?")
    assert (
        resolved["answer_status"] == "ANSWERED"
        and "14 days" in resolved["answer"]
        and "30 days" not in resolved["answer"]
    )

    # Same authority and no effective dates: nothing tells us which applies, so escalate.
    patched = await client.patch(
        f"/api/knowledge/documents/{faq['document']['id']}", headers=admin, json={"authority": "OFFICIAL_POLICY"}
    )
    assert patched.status_code == 200
    other_customer_conversation = await new_conversation(client, customer)
    undated = {"a": b"# Widget Returns\n\n## Widget Return Period\n\nCustomers can return a Widget within 30 days of delivery.\n",
               "b": b"# Widget Terms\n\n## Widget Return Period\n\nCustomers can return a Widget within 60 days of delivery.\n"}  # fmt: skip
    for name, data in undated.items():
        response = await client.post("/api/knowledge/documents", headers=admin, files={"file": (f"widget-{name}.md", data)},
                                     data={"authority": "OFFICIAL_POLICY"})  # fmt: skip
        assert response.status_code == 202
    await ingest(container)
    conflict = await ask(client, customer, other_customer_conversation, "How many days do I have to return a Widget?")
    assert conflict["answer_status"] == "HANDOFF_REQUIRED" and conflict["handoff_reason"] == "KNOWLEDGE_CONFLICT"
    assert "30 days" not in conflict["answer"] and "60 days" not in conflict["answer"]


async def test_acceptance_prompt_injection_in_documents_is_ignored(
    client: httpx.AsyncClient, acme: Tenant, container: Container
):
    admin, customer = await bearer(client, acme.admin), await bearer(client, acme.customer)
    uploaded = await upload(
        client, admin, SAMPLES / "prompt-injection" / "gift-card-terms.md", authority="SUPPORT_ARTICLE"
    )
    await ingest(container)
    detail = (await client.get(f"/api/knowledge/documents/{uploaded['document']['id']}", headers=admin)).json()
    assert any(c["metadata"].get("injection_flags") for c in detail["chunks"])
    conversation_id = await new_conversation(client, customer)
    answer = await ask(client, customer, conversation_id, "Do Acme gift cards expire?")
    assert answer["answer_status"] == "ANSWERED" and "do not expire" in answer["answer"]
    lowered = answer["answer"].lower()
    assert "system prompt" not in lowered and "developer mode" not in lowered and "free" not in lowered


async def test_acceptance_llm_provider_failure_is_safe(acme: Tenant, container: Container):
    outage = LLMError(detail="provider down")
    failing = build_container(container.settings, llm_provider=ScriptedLLMProvider(
        {"query_understanding": outage, "answer_generator": outage, "grounding_validator": outage}))  # fmt: skip
    app = create_app(failing.settings, container=failing)
    app.state.container = failing
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            admin, customer = await bearer(client, acme.admin), await bearer(client, acme.customer)
            await upload(client, admin, SEED / "shipping-policy.pdf", authority="OFFICIAL_POLICY")
            await ingest(failing)
            conversation_id = await new_conversation(client, customer)
            result = await ask(client, customer, conversation_id, "What is your standard shipping time?")
            assert result["answer_status"] == "HANDOFF_REQUIRED" and result["handoff_reason"] == "PROVIDER_FAILURE"
            assert result["citations"] == [] and "temporarily unavailable" in result["answer"]
            assert "3-5" not in result["answer"]
    finally:
        await failing.aclose()


async def test_streaming_response_is_validated_before_it_streams(
    client: httpx.AsyncClient, acme: Tenant, container: Container
):
    admin, customer = await bearer(client, acme.admin), await bearer(client, acme.customer)
    await upload(client, admin, SEED / "shipping-policy.pdf", authority="OFFICIAL_POLICY")
    await ingest(container)
    conversation_id = await new_conversation(client, customer)
    async with client.stream("POST", f"/api/conversations/{conversation_id}/messages/stream", headers=customer,
                             json={"content": "What is your standard shipping time?"}) as response:  # fmt: skip
        assert response.headers["content-type"].startswith("text/event-stream")
        raw = "".join([chunk async for chunk in response.aiter_text()])
    events = [(block.split("\n")[0].removeprefix("event: "), json.loads(block.split("\n")[1].removeprefix("data: ")))
              for block in raw.strip().split("\n\n") if block.startswith("event:")]  # fmt: skip
    names = [name for name, _ in events]
    assert names[0] == "status" and names[-1] == "done" and "delta" in names
    done = events[-1][1]
    assert (
        done["answer_status"] == "ANSWERED" and "".join(e["text"] for n, e in events if n == "delta") == done["answer"]
    )


async def test_message_creation_is_idempotent(client: httpx.AsyncClient, acme: Tenant, container: Container):
    admin, customer = await bearer(client, acme.admin), await bearer(client, acme.customer)
    await upload(client, admin, SEED / "shipping-policy.pdf", authority="OFFICIAL_POLICY")
    await ingest(container)
    conversation_id = await new_conversation(client, customer)
    first = await ask(
        client, customer, conversation_id, "What is your standard shipping time?", client_message_id="retry-1"
    )
    again = await ask(
        client, customer, conversation_id, "What is your standard shipping time?", client_message_id="retry-1"
    )
    assert first["message_id"] == again["message_id"]
    detail = (await client.get(f"/api/conversations/{conversation_id}", headers=customer)).json()
    assert len(detail["messages"]) == 2


async def test_admin_retrieval_debug_and_document_management(
    client: httpx.AsyncClient, acme: Tenant, container: Container
):
    admin = await bearer(client, acme.admin)
    uploaded = await upload(client, admin, SEED / "account-faq.html", authority="FAQ")
    await ingest(container)
    debug = (
        await client.post("/api/admin/retrieval/debug", headers=admin, json={"query": "How do I reset my password?"})
    ).json()
    assert (
        debug["sufficiency"]["sufficient"]
        and debug["selected_evidence"][0]["section_title"] == "How do I reset my password?"
    )
    document_id = uploaded["document"]["id"]
    assert (await client.post(f"/api/knowledge/documents/{document_id}/reindex", headers=admin)).status_code == 202
    assert (await client.post(f"/api/knowledge/documents/{document_id}/reindex", headers=admin)).status_code == 409
    assert (await client.delete(f"/api/knowledge/documents/{document_id}", headers=admin)).status_code == 204
    listing = (await client.get("/api/knowledge/documents", headers=admin)).json()
    assert listing["total"] == 0
    audit = (await client.get("/api/admin/audit-logs", headers=admin)).json()
    assert {"document.uploaded", "document.reindex_requested", "document.deleted"} <= {
        a["action"] for a in audit["items"]
    }

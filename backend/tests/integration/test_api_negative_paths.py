"""Negative-path API tests: bad input, wrong role, wrong tenant, impossible state changes.

Each case asserts the status code *and* the error code a real client would receive, so a
regression that turns a 403 into a 500 - or silently allows a forbidden action - fails here.
Every group also asserts the matching positive case, so the tests cannot pass by rejecting
everything.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.container import Container, build_container
from app.main import create_app
from tests.conftest import make_settings
from tests.integration.conftest import Tenant, bearer, create_tenant, ingest

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
SEED = BACKEND / "seed" / "acme"
MISSING = uuid.uuid4()


def code(response: httpx.Response) -> str:
    return str(response.json()["error"]["code"])


async def start(client: httpx.AsyncClient, headers: dict[str, str], first_message: str | None = None) -> str:
    response = await client.post("/api/conversations", headers=headers, json={"first_message": first_message})
    assert response.status_code in (200, 201), response.text
    return str(response.json()["id"])


async def send(
    client: httpx.AsyncClient, headers: dict[str, str], conversation_id: str, content: str
) -> httpx.Response:
    return await client.post(
        f"/api/conversations/{conversation_id}/messages", headers=headers, json={"content": content}
    )


async def upload(
    client: httpx.AsyncClient, headers: dict[str, str], name: str, data: bytes, **form: str
) -> httpx.Response:
    return await client.post("/api/knowledge/documents", headers=headers, files={"file": (name, data)}, data=form)


@asynccontextmanager
async def app_with(container: Container, tmp_path: Path, **overrides: Any) -> AsyncIterator[httpx.AsyncClient]:
    """A second app on the same database, with different settings (limits, providers)."""
    settings = make_settings(tmp_path, database_url=container.settings.database_url, **overrides)
    built = build_container(settings)
    app = create_app(settings, container=built)
    app.state.container = built
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        await built.aclose()


async def test_unknown_and_malformed_identifiers(client: httpx.AsyncClient, acme: Tenant):
    customer = await bearer(client, acme.customer)
    conversation_id = await start(client, customer)

    malformed = await client.get("/api/conversations/not-a-uuid", headers=customer)
    assert malformed.status_code == 422 and code(malformed) == "VALIDATION_ERROR"
    unknown = await client.get(f"/api/conversations/{MISSING}", headers=customer)
    assert unknown.status_code == 404 and code(unknown) == "NOT_FOUND"
    assert (await send(client, customer, str(MISSING), "Hello?")).status_code == 404

    assert (await client.get(f"/api/conversations/{conversation_id}", headers=customer)).status_code == 200


async def test_message_content_bounds(client: httpx.AsyncClient, acme: Tenant):
    customer = await bearer(client, acme.customer)
    conversation_id = await start(client, customer)

    empty = await send(client, customer, conversation_id, "")
    assert empty.status_code == 422 and code(empty) == "VALIDATION_ERROR"
    blank = await send(client, customer, conversation_id, "    ")
    assert blank.status_code == 422 and code(blank) == "VALIDATION_ERROR"
    too_long = await send(client, customer, conversation_id, "a" * 4001)
    assert too_long.status_code == 422 and code(too_long) == "VALIDATION_ERROR"

    assert (await send(client, customer, conversation_id, "What are your support hours?")).status_code == 200


async def test_pagination_and_filter_bounds(client: httpx.AsyncClient, acme: Tenant):
    customer = await bearer(client, acme.customer)

    for params in ({"page": 0}, {"page_size": 0}, {"page_size": 101}, {"status": "NOT_A_STATUS"}, {"q": "x" * 201}):
        response = await client.get("/api/conversations", headers=customer, params=params)
        assert response.status_code == 422, f"{params} should be rejected: {response.text}"

    ok = await client.get(
        "/api/conversations", headers=customer, params={"page": 1, "page_size": 100, "status": "OPEN"}
    )
    assert ok.status_code == 200 and ok.json()["page_size"] == 100


async def test_feedback_is_only_accepted_on_support_responses(client: httpx.AsyncClient, acme: Tenant):
    customer = await bearer(client, acme.customer)
    conversation_id = await start(client, customer)
    await send(client, customer, conversation_id, "What are your support hours?")
    detail = (await client.get(f"/api/conversations/{conversation_id}", headers=customer)).json()
    user_message = next(m for m in detail["messages"] if m["role"] == "USER")
    assistant_message = next(m for m in detail["messages"] if m["role"] == "ASSISTANT")

    async def rate(body: dict[str, Any]) -> httpx.Response:
        return await client.post(f"/api/conversations/{conversation_id}/feedback", headers=customer, json=body)

    own = await rate({"message_id": user_message["id"], "rating": "HELPFUL"})
    assert own.status_code == 422 and code(own) == "VALIDATION_ERROR"
    unknown = await rate({"message_id": str(MISSING), "rating": "HELPFUL"})
    assert unknown.status_code == 404 and code(unknown) == "NOT_FOUND"
    assert (await rate({"message_id": assistant_message["id"], "rating": "MAYBE"})).status_code == 422
    assert (await rate({"message_id": assistant_message["id"]})).status_code == 422

    assert (await rate({"message_id": assistant_message["id"], "rating": "HELPFUL"})).status_code == 204
    replaced = await rate({"message_id": assistant_message["id"], "rating": "NOT_HELPFUL", "reason": "INCORRECT"})
    assert replaced.status_code == 204


async def test_closed_conversation_refuses_new_activity(client: httpx.AsyncClient, acme: Tenant):
    customer, agent = await bearer(client, acme.customer), await bearer(client, acme.agent)
    conversation_id = await start(client, customer)
    await send(client, customer, conversation_id, "What are your support hours?")
    assert (await client.post(f"/api/conversations/{conversation_id}/close", headers=customer)).status_code == 200

    blocked = await send(client, customer, conversation_id, "One more question")
    assert blocked.status_code == 409 and code(blocked) == "CONFLICT"
    handoff = await client.post(f"/api/conversations/{conversation_id}/handoff", headers=customer, json={})
    assert handoff.status_code == 409 and code(handoff) == "CONFLICT"
    reply = await client.post(
        f"/api/admin/conversations/{conversation_id}/messages", headers=agent, json={"content": "Hello?"}
    )
    assert reply.status_code == 409 and code(reply) == "CONFLICT"

    assert (await client.get(f"/api/conversations/{conversation_id}", headers=customer)).status_code == 200


async def test_role_boundaries_on_staff_and_admin_endpoints(client: httpx.AsyncClient, acme: Tenant):
    customer, agent, admin = (
        await bearer(client, acme.customer),
        await bearer(client, acme.agent),
        await bearer(client, acme.admin),
    )
    conversation_id = await start(client, customer)
    await send(client, customer, conversation_id, "What are your support hours?")

    forbidden = await client.get("/api/admin/conversations", headers=customer)
    assert forbidden.status_code == 403 and code(forbidden) == "AUTHORIZATION_ERROR"
    assert (await client.get("/api/knowledge/documents", headers=customer)).status_code == 403
    assert (await client.get("/api/knowledge/documents", headers=agent)).status_code == 403
    assert (await upload(client, agent, "note.md", b"# Note\n\nHello.")).status_code == 403
    assert (await client.delete(f"/api/admin/conversations/{conversation_id}", headers=agent)).status_code == 403
    assert (
        await client.post("/api/admin/retrieval/debug", headers=agent, json={"query": "shipping"})
    ).status_code == 403
    assert (await client.get("/api/admin/audit-logs", headers=agent)).status_code == 403

    assert (await client.get("/api/admin/conversations", headers=agent)).status_code == 200
    assert (await client.get("/api/admin/audit-logs", headers=admin)).status_code == 200


async def test_handoff_assignment_rules(client: httpx.AsyncClient, acme: Tenant):
    customer, agent, admin = (
        await bearer(client, acme.customer),
        await bearer(client, acme.agent),
        await bearer(client, acme.admin),
    )
    conversation_id = await start(client, customer)
    await send(client, customer, conversation_id, "What are your support hours?")
    await client.post(f"/api/conversations/{conversation_id}/handoff", headers=customer, json={"note": "Need a person"})
    handoff = (await client.get("/api/admin/handoffs", headers=agent)).json()["items"][0]

    to_customer = await client.post(
        f"/api/admin/handoffs/{handoff['id']}/assign", headers=admin, json={"agent_id": str(acme.customer.id)}
    )
    assert to_customer.status_code == 422 and code(to_customer) == "VALIDATION_ERROR"
    to_other = await client.post(
        f"/api/admin/handoffs/{handoff['id']}/assign", headers=agent, json={"agent_id": str(acme.admin.id)}
    )
    assert to_other.status_code == 422 and code(to_other) == "VALIDATION_ERROR"
    assert (await client.post(f"/api/admin/handoffs/{MISSING}/assign", headers=agent, json={})).status_code == 404

    assert (await client.post(f"/api/admin/handoffs/{handoff['id']}/assign", headers=agent, json={})).status_code == 200
    assert (await client.post(f"/api/admin/handoffs/{handoff['id']}/resolve", headers=agent)).status_code == 200
    reassigned = await client.post(f"/api/admin/handoffs/{handoff['id']}/assign", headers=agent, json={})
    assert reassigned.status_code == 409 and code(reassigned) == "CONFLICT"


async def test_another_tenant_sees_nothing(client: httpx.AsyncClient, acme: Tenant, container: Container):
    globex = await create_tenant(container, "globex")
    acme_customer, acme_admin = await bearer(client, acme.customer), await bearer(client, acme.admin)
    other_customer, other_admin = await bearer(client, globex.customer), await bearer(client, globex.admin)

    conversation_id = await start(client, acme_customer)
    await send(client, acme_customer, conversation_id, "What are your support hours?")
    document_id = (await upload(client, acme_admin, "hours.md", b"# Hours\n\nSupport is open 9-5.")).json()["document"][
        "id"
    ]

    assert (await client.get(f"/api/conversations/{conversation_id}", headers=other_customer)).status_code == 404
    assert (await client.get(f"/api/admin/conversations/{conversation_id}", headers=other_admin)).status_code == 404
    assert (await client.get(f"/api/knowledge/documents/{document_id}", headers=other_admin)).status_code == 404
    assert (await client.delete(f"/api/knowledge/documents/{document_id}", headers=other_admin)).status_code == 404
    assert (await client.get("/api/admin/conversations", headers=other_admin)).json()["total"] == 0

    assert (await client.get(f"/api/knowledge/documents/{document_id}", headers=acme_admin)).status_code == 200


async def test_uploads_reject_unsupported_types_and_mismatched_versions(client: httpx.AsyncClient, acme: Tenant):
    admin = await bearer(client, acme.admin)

    executable = await upload(client, admin, "tool.exe", b"MZ\x90\x00binary")
    assert executable.status_code == 415 and code(executable) == "UNSUPPORTED_MEDIA_TYPE"
    disguised = await upload(client, admin, "notes.pdf", b"This is plain text, not a PDF.")
    assert disguised.status_code in (415, 422), disguised.text
    assert (await upload(client, admin, "empty.md", b"")).status_code in (415, 422)

    created = await upload(client, admin, "hours.md", b"# Hours\n\nSupport is open 9-5 Monday to Friday.")
    assert created.status_code == 202
    document_id = created.json()["document"]["id"]

    wrong_type = await upload(client, admin, "hours.txt", b"Support is open 9-5.", document_id=document_id)
    assert wrong_type.status_code == 422 and code(wrong_type) == "VALIDATION_ERROR"
    same_type = await upload(
        client, admin, "hours.md", b"# Hours\n\nSupport is open 8-6 Monday to Friday.", document_id=document_id
    )
    assert same_type.status_code == 202


async def test_upload_larger_than_the_limit_is_refused(container: Container, acme: Tenant, tmp_path: Path):
    async with app_with(container, tmp_path, max_upload_size=1024) as client:
        admin = await bearer(client, acme.admin)
        too_big = await upload(client, admin, "big.md", b"# Big\n\n" + b"x" * 2048)
        assert too_big.status_code == 413 and code(too_big) == "PAYLOAD_TOO_LARGE"
        assert (await upload(client, admin, "small.md", b"# Small\n\nSupport is open 9-5.")).status_code == 202


async def test_deleted_documents_cannot_be_changed(client: httpx.AsyncClient, acme: Tenant, container: Container):
    admin = await bearer(client, acme.admin)
    document_id = (await upload(client, admin, "returns.md", (SEED / "refund-policy.md").read_bytes())).json()[
        "document"
    ]["id"]
    await ingest(container)
    assert (await client.delete(f"/api/knowledge/documents/{document_id}", headers=admin)).status_code == 204

    patched = await client.patch(f"/api/knowledge/documents/{document_id}", headers=admin, json={"title": "New title"})
    assert patched.status_code == 409 and code(patched) == "CONFLICT"
    reindexed = await client.post(f"/api/knowledge/documents/{document_id}/reindex", headers=admin)
    assert reindexed.status_code == 409 and code(reindexed) == "CONFLICT"
    new_version = await upload(client, admin, "returns.md", b"# Returns\n\nUpdated.", document_id=document_id)
    assert new_version.status_code == 409 and code(new_version) == "CONFLICT"


async def test_document_status_updates_are_validated(client: httpx.AsyncClient, acme: Tenant):
    admin = await bearer(client, acme.admin)
    document_id = (await upload(client, admin, "hours.md", b"# Hours\n\nSupport is open 9-5.")).json()["document"]["id"]

    deleting = await client.patch(f"/api/knowledge/documents/{document_id}", headers=admin, json={"status": "DELETED"})
    assert deleting.status_code == 422 and code(deleting) == "VALIDATION_ERROR"
    assert (
        await client.patch(f"/api/knowledge/documents/{document_id}", headers=admin, json={"status": "ARCHIVED"})
    ).status_code == 422
    assert (
        await client.patch(f"/api/knowledge/documents/{document_id}", headers=admin, json={"title": ""})
    ).status_code == 422

    deactivated = await client.patch(
        f"/api/knowledge/documents/{document_id}", headers=admin, json={"status": "INACTIVE", "title": "Support hours"}
    )
    assert deactivated.status_code == 200 and deactivated.json()["status"] == "INACTIVE"


async def test_retrieval_debug_and_metrics_input_bounds(client: httpx.AsyncClient, acme: Tenant):
    admin, agent = await bearer(client, acme.admin), await bearer(client, acme.agent)

    assert (await client.post("/api/admin/retrieval/debug", headers=admin, json={"query": ""})).status_code == 422
    assert (
        await client.post("/api/admin/retrieval/debug", headers=admin, json={"query": "x" * 1001})
    ).status_code == 422
    assert (await client.post("/api/admin/retrieval/debug", headers=admin, json={})).status_code == 422
    for days in (0, 366, -1):
        assert (await client.get("/api/admin/metrics", headers=agent, params={"days": days})).status_code == 422

    assert (
        await client.post("/api/admin/retrieval/debug", headers=admin, json={"query": "shipping"})
    ).status_code == 200
    assert (await client.get("/api/admin/metrics", headers=agent, params={"days": 30})).status_code == 200


async def test_requests_without_a_valid_token_are_rejected(client: httpx.AsyncClient, acme: Tenant):
    valid = await bearer(client, acme.customer)
    token = valid["Authorization"].removeprefix("Bearer ")
    client.cookies.clear()  # logging in also set a session cookie on this client

    assert (await client.get("/api/conversations")).status_code == 401
    assert (await client.get("/api/conversations", headers={"Authorization": "Bearer not.a.token"})).status_code == 401
    tampered = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
    response = await client.get("/api/conversations", headers={"Authorization": f"Bearer {tampered}"})
    assert response.status_code == 401 and code(response) == "AUTHENTICATION_ERROR"

    assert (await client.get("/api/conversations", headers=valid)).status_code == 200


async def test_chat_rate_limit_returns_429_with_retry_after(container: Container, acme: Tenant, tmp_path: Path):
    async with app_with(container, tmp_path, rate_limit_enabled=True, rate_limit_chat="1/minute") as client:
        customer = await bearer(client, acme.customer)
        conversation_id = await start(client, customer)

        assert (await send(client, customer, conversation_id, "What are your support hours?")).status_code == 200
        limited = await send(client, customer, conversation_id, "And what about weekends?")
        assert limited.status_code == 429 and code(limited) == "RATE_LIMITED"
        assert limited.headers.get("Retry-After")

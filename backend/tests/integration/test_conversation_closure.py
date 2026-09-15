"""Who closed a conversation, and when, is recorded and shown to customers and staff."""

from __future__ import annotations

from datetime import datetime

import httpx
import pytest

from tests.integration.conftest import Tenant, bearer

pytestmark = pytest.mark.integration


async def _conversation(client: httpx.AsyncClient, headers: dict) -> str:
    response = await client.post("/api/conversations", headers=headers, json={})
    assert response.status_code == 201
    return response.json()["id"]


async def test_customer_close_records_closer_and_timestamp(client: httpx.AsyncClient, acme: Tenant):
    customer, agent = await bearer(client, acme.customer), await bearer(client, acme.agent)
    conversation_id = await _conversation(client, customer)

    closed = await client.post(f"/api/conversations/{conversation_id}/close", headers=customer)
    body = closed.json()
    assert closed.status_code == 200 and body["status"] == "CLOSED"
    assert body["closed_by"] == {"id": str(acme.customer.id), "name": acme.customer.name, "role": "CUSTOMER"}
    closed_at = datetime.fromisoformat(body["closed_at"])

    detail = (await client.get(f"/api/conversations/{conversation_id}", headers=customer)).json()
    event = detail["messages"][-1]
    assert detail["conversation"]["closed_by"]["name"] == acme.customer.name
    assert event["role"] == "SYSTEM" and event["content"] == f"Conversation closed by {acme.customer.name}."
    assert datetime.fromisoformat(event["created_at"]) == closed_at

    history = (await client.get("/api/conversations", headers=customer)).json()["items"]
    assert history[0]["closed_by"]["name"] == acme.customer.name

    staff_list = (await client.get("/api/admin/conversations", headers=agent)).json()["items"]
    assert staff_list[0]["closed_by"]["id"] == str(acme.customer.id)


async def test_agent_close_is_attributed_to_the_agent(client: httpx.AsyncClient, acme: Tenant):
    customer, agent = await bearer(client, acme.customer), await bearer(client, acme.agent)
    conversation_id = await _conversation(client, customer)

    closed = await client.post(f"/api/admin/conversations/{conversation_id}/close", headers=agent)
    assert closed.status_code == 200
    # Staff see the full name of the colleague who closed it.
    assert closed.json()["closed_by"] == {"id": str(acme.agent.id), "name": acme.agent.name, "role": "AGENT"}

    # Customers see staff by first name with a Support label, as with agent replies.
    customer_view = (await client.get(f"/api/conversations/{conversation_id}", headers=customer)).json()
    support_name = f"{acme.agent.name.split()[0]} (Support)"
    assert customer_view["conversation"]["closed_by"]["name"] == support_name
    assert customer_view["messages"][-1]["content"] == f"Conversation closed by {support_name}."

    again = await client.post(f"/api/admin/conversations/{conversation_id}/close", headers=agent)
    assert again.status_code == 200
    events = [m for m in customer_view["messages"] if m["role"] == "SYSTEM"]
    refreshed = (await client.get(f"/api/conversations/{conversation_id}", headers=customer)).json()
    assert (
        len([m for m in refreshed["messages"] if m["role"] == "SYSTEM"]) == len(events) == 1
    )  # closing twice adds nothing

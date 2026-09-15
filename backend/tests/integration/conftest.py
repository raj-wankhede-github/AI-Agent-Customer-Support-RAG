"""PostgreSQL + pgvector fixtures.

Uses TEST_DATABASE_URL (default: the `support_test` database created by docker compose).
Refuses to run against a database whose name does not end in `_test`, applies Alembic
migrations once per session, and truncates every table before each test.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from app.cli import process_queue
from app.core.container import Container, build_container
from app.db.base import Base
from app.main import create_app
from app.models import Company, User
from app.models.enums import UserRole
from app.security.principal import Principal
from app.services.auth import create_user
from tests.conftest import DEFAULT_TEST_DATABASE_URL, make_settings

BACKEND = Path(__file__).resolve().parents[2]
PASSWORD = "Integration-Test-Password-1"

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL)
    if not url.rsplit("/", 1)[-1].endswith("_test"):
        pytest.exit("Refusing to run integration tests against a database not named *_test", returncode=2)
    return url


@pytest.fixture(scope="session")
async def migrated(database_url: str) -> str:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"PostgreSQL is not reachable for integration tests: {type(exc).__name__}")
    finally:
        await engine.dispose()
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    os.environ["DATABASE_URL"] = database_url
    await asyncio.to_thread(command.upgrade, config, "head")
    return database_url


@pytest.fixture
async def container(migrated: str, tmp_path: Path) -> AsyncIterator[Container]:
    settings = make_settings(tmp_path, database_url=migrated)
    built = build_container(settings)
    tables = ", ".join(table.name for table in reversed(Base.metadata.sorted_tables))
    async with built.engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {tables} CASCADE"))
    yield built
    await built.aclose()


@dataclass
class Tenant:
    company: Company
    admin: User
    agent: User
    customer: User

    def principal(self, user: User) -> Principal:
        return Principal(user.id, self.company.id, self.company.name, UserRole(user.role), user.email, user.name,
                         uuid.uuid4().hex, datetime.now(UTC))  # fmt: skip


async def create_tenant(container: Container, slug: str) -> Tenant:
    async with container.sessions() as session:
        company = Company(slug=slug, name=f"{slug.title()} Inc")
        session.add(company)
        await session.flush()
        users = [
            await create_user(session, company_id=company.id, email=f"{role.value.lower()}@{slug}.test",
                              name=f"{slug.title()} {role.value.title()}", role=role, password=PASSWORD)
            for role in (UserRole.ADMIN, UserRole.AGENT, UserRole.CUSTOMER)
        ]  # fmt: skip
        await session.commit()
        return Tenant(company, *users)


@pytest.fixture
async def acme(container: Container) -> Tenant:
    return await create_tenant(container, "acme")


@pytest.fixture
async def client(container: Container) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(container.settings, container=container)
    app.state.container = container  # ASGITransport does not run lifespan events
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http:
        yield http


async def bearer(client: httpx.AsyncClient, user: User) -> dict[str, str]:
    response = await client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def ingest(container: Container) -> int:
    return await process_queue(container)

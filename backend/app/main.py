"""FastAPI application factory.

Development:  uvicorn app.main:create_app --factory --reload
Production:   gunicorn -c gunicorn.conf.py "app.main:create_app()"   (see Dockerfile)
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_error_handlers
from app.api.routes import admin, auth, conversations, health, knowledge
from app.core.config import Settings, get_settings
from app.core.container import Container, build_container
from app.observability.logging import configure_logging
from app.observability.middleware import RequestContextMiddleware
from app.workers.ingestion_worker import IngestionWorker
from app.workers.maintenance import MaintenanceScheduler

log = structlog.get_logger(__name__)

DESCRIPTION = """
Grounded customer-support assistant API.

**Answers come only from the approved knowledge base.** Every answer carries citations to
retrieved chunks; when evidence is missing, weak, contradictory or unverifiable the assistant
abstains or hands the conversation to a human.

### Authentication
`POST /api/auth/login` returns a JWT and sets an HttpOnly session cookie.
* API clients: send `Authorization: Bearer <token>` (use **Authorize** above).
* Browsers: the cookie is sent automatically; state-changing requests must include
  `X-Requested-With: XMLHttpRequest`.

### Roles
* **CUSTOMER** - own conversations only.
* **AGENT** - handoff queue, all tenant conversations, replies to customers.
* **ADMIN** - everything agents can do plus knowledge-base management, retrieval debugging,
  audit logs and conversation deletion.

### Errors
All errors use `{"error": {"code", "message", "request_id"}}` with codes such as
`VALIDATION_ERROR`, `AUTHENTICATION_ERROR`, `AUTHORIZATION_ERROR`, `NOT_FOUND`, `RATE_LIMITED`,
`LLM_ERROR`, `RETRIEVAL_ERROR`, `DATABASE_ERROR` and `INTERNAL_ERROR`.
"""

TAGS = [
    {"name": "Authentication", "description": "Sessions and the current user."},
    {"name": "Conversations", "description": "Customer conversations, the grounded chat turn, feedback and handoff."},
    {"name": "Knowledge base (admin)", "description": "Upload, version, inspect, deactivate and reindex documents."},
    {
        "name": "Admin & support agents",
        "description": "Handoff queue, human replies, oversight, metrics and debugging.",
    },
    {"name": "Health", "description": "Liveness and readiness probes."},
]


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json, service="api")

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        current = container or build_container(settings)
        app.state.container = current
        stop = asyncio.Event()
        background: list[asyncio.Task[None]] = []
        if settings.ingestion_worker_embedded:
            # Development convenience; production runs these in the dedicated worker service.
            current.worker = IngestionWorker(current.pipeline, settings)
            background.append(asyncio.create_task(current.worker.run(stop)))
            background.append(asyncio.create_task(MaintenanceScheduler(current.sessions, settings).run(stop)))
        log.info(
            "api_started",
            env=settings.app_env,
            llm_provider=settings.llm_provider,
            embedding_model=current.embedder.model,
        )
        try:
            yield
        finally:
            stop.set()
            for task in background:
                with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=15)
            if container is None:
                await current.aclose()
            log.info("api_stopped")

    docs = settings.expose_api_docs
    app = FastAPI(
        title=f"{settings.app_name} API",
        version="1.0.0",
        description=DESCRIPTION,
        openapi_tags=TAGS,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Requested-With", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )
    app.add_middleware(RequestContextMiddleware, hsts=settings.app_env == "production")
    register_error_handlers(app)
    for module in (health, auth, conversations, knowledge, admin):
        app.include_router(module.router)
    return app

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api.deps import AppContainer

router = APIRouter(tags=["Health"])


@router.get("/health", summary="Liveness", description="The process is up. Does not touch dependencies.")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get(
    "/ready",
    summary="Readiness",
    description="Checks the database (including pgvector) and document storage. Returns 503 when not ready.",
    responses={503: {"description": "A dependency is unavailable"}},
)
async def ready(container: AppContainer) -> JSONResponse:
    checks: dict[str, Any] = {}

    async def database() -> bool:
        async with container.engine.connect() as conn:
            return bool(await conn.scalar(text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")))

    for name, probe in (("database", database), ("storage", container.storage.healthcheck)):
        try:
            checks[name] = bool(await asyncio.wait_for(probe(), timeout=3))
        except Exception:
            checks[name] = False
    ok = all(checks.values())
    body = {
        "status": "ready" if ok else "not_ready",
        "checks": checks,
        "llm_provider": container.settings.llm_provider,
        "embedding_model": container.embedder.model,
    }
    return JSONResponse(body, status_code=200 if ok else 503)

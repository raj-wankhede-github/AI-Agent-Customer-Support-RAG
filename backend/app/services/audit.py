"""Security audit log. Records who did what to which resource - never message content,
passwords, tokens or file contents."""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

log = structlog.get_logger("audit")


def record_audit(
    session: AsyncSession,
    *,
    action: str,
    company_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    target_type: str | None = None,
    target_id: object | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Adds the audit row to the caller's transaction, so it commits (or rolls back) with
    the action it describes."""
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    session.add(
        AuditLog(
            company_id=company_id,
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            request_id=request_id,
            details=details or {},
        )
    )
    log.info("audit", action=action, target_type=target_type, target_id=str(target_id) if target_id else None)

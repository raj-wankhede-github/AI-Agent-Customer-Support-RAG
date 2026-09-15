"""Conversation retention. Deletes conversations (and, via cascades, their messages,
traces, feedback and handoffs) whose last activity is older than the retention window.

Invoked explicitly (`python -m app.cli purge-conversations`) - e.g. from a scheduled
ECS task or Kubernetes CronJob. Nothing is deleted automatically at startup, and a
retention of 0 days disables purging.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog, Conversation

log = structlog.get_logger(__name__)


async def purge_conversations(session: AsyncSession, retention_days: int, *, dry_run: bool = False) -> int:
    if retention_days <= 0:
        log.info("retention_disabled")
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    condition = Conversation.last_message_at < cutoff
    count = await session.scalar(select(func.count()).where(condition)) or 0
    if dry_run or count == 0:
        return count
    await session.execute(delete(Conversation).where(condition))
    session.add(
        AuditLog(action="retention.conversations_purged", details={"count": count, "retention_days": retention_days})
    )
    await session.commit()
    log.info("retention_purged", count=count, retention_days=retention_days)
    return count

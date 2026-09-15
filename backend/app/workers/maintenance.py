"""Periodic background maintenance (runs alongside the ingestion worker).

Currently: automatically close resolved conversations that received no customer reply within
RESOLVED_AUTO_CLOSE_DAYS. Each run is idempotent and safe with several replicas, so it can also
be triggered externally (`python -m app.cli close-resolved`) from a scheduler.
"""

from __future__ import annotations

import asyncio
import contextlib

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.services.lifecycle import auto_close_resolved

log = structlog.get_logger(__name__)


class MaintenanceScheduler:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], settings: Settings) -> None:
        self.sessions = sessions
        self.settings = settings

    async def run(self, stop: asyncio.Event) -> None:
        if self.settings.resolved_auto_close_days <= 0:
            log.info("maintenance_disabled")
            return
        log.info("maintenance_started", interval_s=self.settings.maintenance_interval_seconds)
        while not stop.is_set():
            try:
                await auto_close_resolved(self.sessions, self.settings)
            except Exception:
                log.exception("maintenance_run_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.settings.maintenance_interval_seconds)
        log.info("maintenance_stopped")

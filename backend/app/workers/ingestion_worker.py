"""Background ingestion worker.

Polls PostgreSQL for UPLOADED document versions and processes them. The database is the
queue (claims use FOR UPDATE SKIP LOCKED), so any number of worker replicas can run and
work survives restarts without Redis. To move to SQS/RabbitMQ later, replace
`claim_next()` with a queue consumer; `process_version()` stays the same.

Run as a dedicated service:  python -m app.workers.ingestion_worker
or embedded in the API process with INGESTION_WORKER_EMBEDDED=true (development).
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

import structlog

from app.core.config import Settings, get_settings
from app.ingestion.pipeline import IngestionPipeline
from app.observability.logging import configure_logging

log = structlog.get_logger(__name__)


class IngestionWorker:
    def __init__(self, pipeline: IngestionPipeline, settings: Settings) -> None:
        self.pipeline = pipeline
        self.settings = settings
        self._wake = asyncio.Event()

    def notify(self) -> None:
        """Wake the worker immediately (e.g. right after an upload in the same process)."""
        self._wake.set()

    async def run(self, stop: asyncio.Event) -> None:
        log.info("ingestion_worker_started")
        failures = 0
        while not stop.is_set():
            try:
                version_id = await self.pipeline.claim_next()
                failures = 0
            except Exception:
                failures += 1
                log.exception("ingestion_claim_failed", consecutive_failures=failures)
                await self._sleep(stop, min(60.0, self.settings.ingestion_poll_interval_seconds * 2**failures))
                continue
            if version_id is not None:
                await self.pipeline.process_version(version_id)
                continue
            await self._sleep(stop, self.settings.ingestion_poll_interval_seconds)
        log.info("ingestion_worker_stopped")

    async def _sleep(self, stop: asyncio.Event, seconds: float) -> None:
        self._wake.clear()
        stop_task = asyncio.ensure_future(stop.wait())
        wake_task = asyncio.ensure_future(self._wake.wait())
        _, pending = await asyncio.wait({stop_task, wake_task}, timeout=seconds, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in (stop_task, wake_task):
            if not task.done():
                task.cancel()


async def _main() -> None:
    from app.core.container import build_container

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json, service="worker")
    container = build_container(settings)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):  # not available on Windows event loops
            loop.add_signal_handler(sig, stop.set)
    try:
        from app.workers.maintenance import MaintenanceScheduler

        await asyncio.gather(
            IngestionWorker(container.pipeline, settings).run(stop),
            MaintenanceScheduler(container.sessions, settings).run(stop),
        )
    finally:
        await container.aclose()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())

"""Bounded exponential backoff with jitter for transient external-provider failures."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable

import structlog

log = structlog.get_logger(__name__)


async def retry_async[T](
    operation: Callable[[], Awaitable[T]],
    *,
    is_transient: Callable[[Exception], bool],
    max_retries: int,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    event: str = "provider_retry",
) -> T:
    """Run `operation`, retrying only transient failures, at most `max_retries` times."""
    attempt = 0
    while True:
        try:
            return await operation()
        except Exception as exc:
            if attempt >= max_retries or not is_transient(exc):
                raise
            jitter = 0.5 + random.random() / 2  # noqa: S311 - not security sensitive
            delay = min(max_delay, base_delay * 2**attempt) * jitter
            attempt += 1
            log.warning(event, attempt=attempt, delay_s=round(delay, 2), error=type(exc).__name__)
            await asyncio.sleep(delay)

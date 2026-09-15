"""Lightweight stage timing.

`Timings.span()` records stage latencies that are persisted with the RAG trace and
logged. It is intentionally shaped like an OpenTelemetry span so a real tracer can be
dropped in here later without touching call sites.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

import structlog

log = structlog.get_logger(__name__)


class Timings:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self._start = time.perf_counter()

    @contextmanager
    def span(self, name: str) -> Iterator[None]:
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = int((time.perf_counter() - start) * 1000)
            self.values[name] = self.values.get(name, 0) + elapsed
            log.debug("span_end", span=name, latency_ms=elapsed)

    def total_ms(self) -> int:
        return int((time.perf_counter() - self._start) * 1000)

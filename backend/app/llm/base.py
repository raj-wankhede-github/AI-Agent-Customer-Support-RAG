"""Provider-neutral LLM interface. Business logic depends only on this module."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, other: LLMUsage) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens


@dataclass
class LLMResponse:
    text: str
    model: str
    latency_ms: int
    usage: LLMUsage = field(default_factory=LLMUsage)


class LLMProvider(Protocol):
    name: str
    model: str

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Return text that the provider constrained to `schema` (still validated by caller).

        Raises `LLMError` (transient or not), `LLMRefusalError` or `LLMOutputError`.
        """
        ...

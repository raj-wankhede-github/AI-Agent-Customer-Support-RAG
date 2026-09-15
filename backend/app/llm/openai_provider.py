"""OpenAI Chat Completions with strict JSON-schema structured output (REST via httpx)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from app.core.errors import LLMError, LLMOutputError, LLMRefusalError
from app.core.retry import retry_async
from app.llm.base import LLMResponse, LLMUsage


def _is_transient(exc: Exception) -> bool:
    return isinstance(exc, LLMError) and exc.transient and not isinstance(exc, LLMOutputError)


class OpenAIProvider:
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float,
        timeout: float,
        max_retries: int,
        max_tokens: int,
    ) -> None:
        self.model = model
        self._temperature = temperature
        self._max_retries = max_retries
        self._max_tokens = max_tokens
        self._client = httpx.AsyncClient(
            base_url=base_url, timeout=timeout, headers={"Authorization": f"Bearer {api_key}"}
        )

    async def _call(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            response = await self._client.post("/chat/completions", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise LLMError(detail=f"OpenAI transport error: {type(exc).__name__}") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise LLMError(detail=f"OpenAI HTTP {response.status_code}")
        if response.status_code >= 400:
            raise LLMError(detail=f"OpenAI rejected request: HTTP {response.status_code}", transient=False)
        return response

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        payload = {
            "model": self.model,
            "temperature": self._temperature,
            "max_completion_tokens": max_tokens or self._max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": schema_name, "strict": True, "schema": schema},
            },
        }
        start = time.perf_counter()
        response = await retry_async(
            lambda: self._call(payload),
            is_transient=_is_transient,
            max_retries=self._max_retries,
            event="llm_retry",
        )
        body = response.json()
        choice = body["choices"][0]
        message = choice["message"]
        if message.get("refusal"):
            raise LLMRefusalError(detail="OpenAI model refused", transient=False)
        if choice.get("finish_reason") == "length":
            raise LLMOutputError(detail="OpenAI output truncated at max tokens")
        usage = body.get("usage") or {}
        return LLMResponse(
            text=message.get("content") or "",
            model=body.get("model", self.model),
            latency_ms=int((time.perf_counter() - start) * 1000),
            usage=LLMUsage(usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)),
        )

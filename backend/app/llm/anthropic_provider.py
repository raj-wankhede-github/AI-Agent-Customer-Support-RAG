"""Claude via the official Anthropic SDK.

- Structured output via `output_config.format` (JSON schema), validated again by the caller.
- Current Claude models reject sampling parameters, so `temperature` is not sent;
  determinism comes from schema-constrained output plus grounding validation.
- Refusals (`stop_reason == "refusal"`) are surfaced as `LLMRefusalError`; with
  `ANTHROPIC_REFUSAL_FALLBACK=true` the API first retries a declined request on
  Anthropic's recommended fallback model server-side.
- The SDK retries 408/409/429/5xx and connection errors with backoff (`max_retries`).
"""

from __future__ import annotations

import time
from typing import Any

import anthropic

from app.core.errors import LLMError, LLMOutputError, LLMRefusalError
from app.llm.base import LLMResponse, LLMUsage

_FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout: float,
        max_retries: int,
        max_tokens: int,
        effort: str,
        refusal_fallback: bool,
    ) -> None:
        self.model = model
        self._max_tokens = max_tokens
        self._effort = effort
        self._refusal_fallback = refusal_fallback
        self._client = anthropic.AsyncAnthropic(api_key=api_key, timeout=timeout, max_retries=max_retries)

    async def complete_json(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if "haiku" not in self.model:  # effort is not supported on Haiku models
            output_config["effort"] = self._effort
        request: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens or self._max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": output_config,
        }
        start = time.perf_counter()
        try:
            if self._refusal_fallback:
                response: Any = await self._client.beta.messages.create(
                    **request, betas=[_FALLBACK_BETA], fallbacks="default"
                )
            else:
                response = await self._client.messages.create(**request)
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise LLMError(detail=f"Anthropic auth error: {type(exc).__name__}", transient=False) from exc
        except (anthropic.BadRequestError, anthropic.NotFoundError) as exc:
            raise LLMError(detail=f"Anthropic rejected request: {exc.message[:200]}", transient=False) from exc
        except anthropic.RateLimitError as exc:
            raise LLMError(detail="Anthropic rate limit exceeded after retries") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(detail=f"Anthropic HTTP {exc.status_code} after retries") from exc
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            raise LLMError(detail=f"Anthropic connection error: {type(exc).__name__}") from exc

        latency_ms = int((time.perf_counter() - start) * 1000)
        if response.stop_reason == "refusal":
            raise LLMRefusalError(detail="Claude declined the request", transient=False)
        if response.stop_reason == "max_tokens":
            raise LLMOutputError(detail="Claude output hit max_tokens before completing the JSON")
        text = next((block.text for block in response.content if block.type == "text"), "")
        usage = LLMUsage(response.usage.input_tokens or 0, response.usage.output_tokens or 0)
        return LLMResponse(text=text, model=response.model, latency_ms=latency_ms, usage=usage)

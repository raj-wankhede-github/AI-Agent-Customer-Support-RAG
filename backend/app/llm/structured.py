"""Schema-constrained generation with strict validation and one corrective retry.

Model output is never parsed with string splitting: providers are asked for JSON that
matches a JSON Schema derived from a Pydantic model, and the result is validated with
that same model. Malformed output gets exactly one correction attempt, then fails safe.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any

import structlog
from pydantic import BaseModel, ValidationError

from app.core.errors import LLMOutputError
from app.llm.base import LLMProvider, LLMUsage
from app.llm.prompts import PromptTemplate

log = structlog.get_logger(__name__)


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic schema -> self-contained strict schema (inlined refs, no extra keys,
    every property required) accepted by OpenAI strict mode and Anthropic structured
    outputs."""
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            node = {k: resolve(v) for k, v in node.items() if k not in ("title", "default")}
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            return node
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    schema: dict[str, Any] = resolve(raw)
    return schema


@dataclass
class StructuredResult[T: BaseModel]:
    value: T
    model: str
    prompt_version: str
    latency_ms: int
    attempts: int
    usage: LLMUsage = field(default_factory=LLMUsage)


class StructuredLLM:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    async def generate[T: BaseModel](
        self,
        *,
        prompt: PromptTemplate,
        user: str,
        output_model: type[T],
        max_tokens: int | None = None,
    ) -> StructuredResult[T]:
        schema = strict_json_schema(output_model)
        usage = LLMUsage()
        latency = 0
        message = user
        last_error = ""
        for attempt in (1, 2):
            response = await self.provider.complete_json(
                system=prompt.system,
                user=message,
                schema=schema,
                schema_name=prompt.name,
                max_tokens=max_tokens,
            )
            usage.add(response.usage)
            latency += response.latency_ms
            try:
                value = output_model.model_validate(json.loads(response.text))
            except (json.JSONDecodeError, ValidationError) as exc:
                last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
                log.warning("llm_output_invalid", prompt=prompt.name, attempt=attempt, error=last_error)
                message = (
                    f"{user}\n\n<correction>Your previous output was not valid for the required "
                    f"JSON schema ({last_error}). Respond again with only a JSON object that "
                    "matches the schema exactly.</correction>"
                )
                continue
            return StructuredResult(
                value=value,
                model=response.model,
                prompt_version=prompt.version_id,
                latency_ms=latency,
                attempts=attempt,
                usage=usage,
            )
        raise LLMOutputError(detail=f"{prompt.name}: invalid structured output after retry: {last_error}")

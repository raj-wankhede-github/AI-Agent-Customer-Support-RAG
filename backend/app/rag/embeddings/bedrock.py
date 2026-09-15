"""Amazon Bedrock embeddings (Titan Text Embeddings v2 request shape).

Uses the standard AWS credential chain; requires the `s3` extra (boto3).
"""

from __future__ import annotations

import asyncio
import json
from functools import partial
from typing import Any

from app.core.errors import EmbeddingError
from app.core.retry import retry_async

_CONCURRENCY = 4


def _is_transient(exc: Exception) -> bool:
    return isinstance(exc, EmbeddingError) and exc.transient


class BedrockEmbeddingProvider:
    name = "bedrock"

    def __init__(self, *, model: str, dimension: int, region: str, max_retries: int) -> None:
        import boto3

        self.model = model
        self.dimension = dimension
        self._max_retries = max_retries
        self._client: Any = boto3.client("bedrock-runtime", region_name=region)
        self._semaphore = asyncio.Semaphore(_CONCURRENCY)

    async def _embed_one(self, text: str) -> list[float]:
        body = json.dumps({"inputText": text, "dimensions": self.dimension, "normalize": True})
        async with self._semaphore:
            try:
                response = await asyncio.to_thread(
                    self._client.invoke_model,
                    modelId=self.model,
                    body=body,
                    contentType="application/json",
                    accept="application/json",
                )
            except Exception as exc:
                code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
                transient = code in {"ThrottlingException", "ServiceUnavailableException", ""}
                raise EmbeddingError(detail=f"Bedrock error {code or type(exc).__name__}", transient=transient) from exc
        vector = json.loads(response["body"].read())["embedding"]
        if len(vector) != self.dimension:
            raise EmbeddingError(detail="Embedding dimension mismatch", transient=False)
        return [float(v) for v in vector]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return list(
            await asyncio.gather(
                *(
                    retry_async(
                        partial(self._embed_one, t),
                        is_transient=_is_transient,
                        max_retries=self._max_retries,
                        event="embedding_retry",
                    )
                    for t in texts
                )
            )
        )

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]

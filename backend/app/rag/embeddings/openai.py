"""OpenAI embeddings over the REST API."""

from __future__ import annotations

from functools import partial

import httpx

from app.core.errors import EmbeddingError
from app.core.retry import retry_async


def _is_transient(exc: Exception) -> bool:
    return isinstance(exc, EmbeddingError) and exc.transient


class OpenAIEmbeddingProvider:
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        dimension: int,
        timeout: float,
        max_retries: int,
        batch_size: int,
    ) -> None:
        self.model = model
        self.dimension = dimension
        self._max_retries = max_retries
        self._batch_size = batch_size
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def _request(self, batch: list[str]) -> list[list[float]]:
        payload: dict[str, object] = {"model": self.model, "input": batch}
        if self.model.startswith("text-embedding-3"):
            payload["dimensions"] = self.dimension
        try:
            response = await self._client.post("/embeddings", json=payload)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise EmbeddingError(detail=f"OpenAI embeddings transport error: {type(exc).__name__}") from exc
        if response.status_code == 429 or response.status_code >= 500:
            raise EmbeddingError(detail=f"OpenAI embeddings HTTP {response.status_code}")
        if response.status_code >= 400:
            raise EmbeddingError(
                detail=f"OpenAI embeddings rejected request: HTTP {response.status_code}",
                transient=False,
            )
        data = sorted(response.json()["data"], key=lambda item: item["index"])
        vectors = [item["embedding"] for item in data]
        if any(len(v) != self.dimension for v in vectors):
            raise EmbeddingError(detail="Embedding dimension does not match VECTOR_DIMENSION", transient=False)
        return vectors

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = texts[start : start + self._batch_size]
            vectors.extend(
                await retry_async(
                    partial(self._request, batch),
                    is_transient=_is_transient,
                    max_retries=self._max_retries,
                    event="embedding_retry",
                )
            )
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]

from __future__ import annotations

from app.core.config import Settings
from app.rag.embeddings.base import EmbeddingProvider


def create_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "openai":
        from app.rag.embeddings.openai import OpenAIEmbeddingProvider

        assert settings.openai_api_key is not None  # enforced by Settings validation
        return OpenAIEmbeddingProvider(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
            model=settings.embedding_model,
            dimension=settings.vector_dimension,
            timeout=settings.embedding_timeout_seconds,
            max_retries=settings.embedding_max_retries,
            batch_size=settings.embedding_batch_size,
        )
    if settings.embedding_provider == "bedrock":
        from app.rag.embeddings.bedrock import BedrockEmbeddingProvider

        return BedrockEmbeddingProvider(
            model=settings.embedding_model,
            dimension=settings.vector_dimension,
            region=settings.aws_region,
            max_retries=settings.embedding_max_retries,
        )
    from app.rag.embeddings.hashing import HashingEmbeddingProvider

    return HashingEmbeddingProvider(settings.vector_dimension, settings.embedding_model)

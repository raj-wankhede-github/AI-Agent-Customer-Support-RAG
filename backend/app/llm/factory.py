from __future__ import annotations

from app.core.config import Settings
from app.llm.base import LLMProvider


def create_llm_provider(settings: Settings) -> LLMProvider | None:
    """Return the configured generative provider, or None for offline extractive mode."""
    if settings.llm_provider == "anthropic":
        from app.llm.anthropic_provider import AnthropicProvider

        assert settings.anthropic_api_key is not None  # enforced by Settings validation
        return AnthropicProvider(
            api_key=settings.anthropic_api_key.get_secret_value(),
            model=settings.effective_llm_model,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            max_tokens=settings.llm_max_tokens,
            effort=settings.llm_effort,
            refusal_fallback=settings.anthropic_refusal_fallback,
        )
    if settings.llm_provider == "openai":
        from app.llm.openai_provider import OpenAIProvider

        assert settings.openai_api_key is not None
        return OpenAIProvider(
            api_key=settings.openai_api_key.get_secret_value(),
            base_url=settings.openai_base_url,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            max_tokens=settings.llm_max_tokens,
        )
    return None

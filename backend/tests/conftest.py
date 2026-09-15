from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings

TEST_JWT_SECRET = "test-jwt-secret-" + "x" * 40
DEFAULT_TEST_DATABASE_URL = "postgresql+asyncpg://support:support@localhost:5432/support_test"


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    """Explicit test settings. Constructor arguments take precedence over .env files, so the
    suite never runs against development configuration or real LLM providers."""
    values: dict[str, Any] = {
        "app_env": "test",
        "jwt_secret": TEST_JWT_SECRET,
        "database_url": os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL),
        "llm_provider": "none",
        "llm_model": "",
        "embedding_provider": "hashing",
        "embedding_model": "hashing-v1",
        "vector_dimension": 384,
        "storage_provider": "local",
        "storage_local_path": str(tmp_path / "uploads"),
        "rate_limit_enabled": False,
        "ingestion_worker_embedded": False,
        "log_json": False,
        "log_level": "WARNING",
        "anthropic_api_key": None,
        "openai_api_key": None,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)

"""Typed, centralized configuration loaded from environment variables.

Every tunable used by the RAG pipeline lives here with the reasoning for its
default, so there are no hidden magic numbers in business logic.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_AUTHORITY_RANKING = (
    "OFFICIAL_POLICY:100,OFFICIAL_DOCUMENTATION:90,PRODUCT_DOCUMENTATION:80,"
    "FAQ:60,SUPPORT_ARTICLE:50,INTERNAL_GUIDE:40,LOW_PRIORITY:10"
)


class Settings(BaseSettings):
    # The repo-root .env is shared with docker compose; backend/.env (if any) overrides it.
    # Real environment variables always take precedence over both files.
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- Application -----------------------------------------------------------------
    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "Acme Support"
    log_level: str = "INFO"
    # JSON logs for machines (production); console rendering is friendlier locally.
    log_json: bool = True
    expose_api_docs: bool = True
    # Only trust X-Forwarded-For when running behind a known reverse proxy / load balancer.
    trust_proxy_headers: bool = False

    # --- Database --------------------------------------------------------------------
    database_url: str = "postgresql+asyncpg://support:support@localhost:5432/support"
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_timeout_seconds: float = 10.0
    # Bounds any single query so a slow statement cannot pin a connection indefinitely.
    db_statement_timeout_ms: int = 15_000

    # --- Auth ------------------------------------------------------------------------
    jwt_secret: SecretStr = SecretStr("")
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_issuer: str = "support-agent"
    jwt_expires_minutes: int = 480
    auth_cookie_name: str = "support_session"
    auth_cookie_secure: bool = False
    cors_allowed_origins: str = "http://localhost:5173"

    # --- LLM -------------------------------------------------------------------------
    # "none" runs the deterministic offline mode: rule-based query understanding and an
    # extractive answer generator. No generative model is called, so nothing can be
    # invented, but answers are verbatim evidence sentences rather than fluent prose.
    llm_provider: Literal["none", "openai", "anthropic"] = "none"
    llm_model: str = ""
    # Factual support answers want the most deterministic sampling available. Providers
    # whose current models reject sampling parameters (Anthropic) ignore this.
    llm_temperature: float = 0.0
    # Generous because reasoning models spend output tokens thinking; hitting the cap
    # truncates structured output, which then fails validation and abstains.
    llm_max_tokens: int = 8_000
    llm_max_input_tokens: int = 12_000
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2
    llm_effort: Literal["low", "medium", "high"] = "medium"
    anthropic_refusal_fallback: bool = True
    openai_api_key: SecretStr | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    anthropic_api_key: SecretStr | None = None

    # --- Embeddings ------------------------------------------------------------------
    # "hashing" is an offline, deterministic feature-hashing embedder (lexical, not truly
    # semantic). Use a hosted provider in production for paraphrase-robust retrieval.
    embedding_provider: Literal["hashing", "openai", "bedrock"] = "hashing"
    embedding_model: str = "hashing-v1"
    vector_dimension: int = 384
    embedding_batch_size: int = 64
    embedding_timeout_seconds: float = 30.0
    embedding_max_retries: int = 3
    aws_region: str = "us-east-1"

    # --- Chunking --------------------------------------------------------------------
    # ~350 tokens (~1,400 chars) holds one policy subsection or FAQ answer: large enough
    # to keep a rule together with its conditions, small enough that one chunk rarely
    # mixes unrelated topics. 500 is a hard cap; ~50 tokens of sentence overlap preserves
    # continuity when a long section must be split.
    chunk_target_tokens: int = 350
    chunk_max_tokens: int = 500
    chunk_overlap_tokens: int = 50

    # --- Retrieval / reranking -------------------------------------------------------
    # Candidates fetched from each retriever (vector and full-text) before fusion.
    rag_top_k: int = 20
    # Minimum cosine similarity for a vector-only candidate to be considered at all.
    rag_min_score: float = 0.15
    # Minimum reranker score (0..1) for a chunk to count as evidence.
    rag_min_rerank_score: float = 0.35
    # Maximum evidence chunks passed to generation. Fewer, better chunks beat noisy context.
    rag_max_context: int = 5
    rag_max_context_tokens: int = 3_000
    reranker_enabled: bool = True
    reranker_provider: Literal["heuristic", "llm"] = "heuristic"
    # Fraction of the question's key terms that selected evidence must cover.
    rag_min_query_coverage: float = 0.6
    # With a semantic embedding provider, a chunk this similar may satisfy coverage even
    # when wording differs. Leave unset for the lexical hashing embedder.
    rag_semantic_override_similarity: float | None = None
    # Near-duplicate chunks (token Jaccard >= this) are collapsed.
    rag_dedup_similarity: float = 0.85
    source_authority_ranking: str = DEFAULT_AUTHORITY_RANKING
    # Reranker weights (sum to 1). Lexical coverage dominates because it is what the
    # grounding validator later checks; vector similarity rescues paraphrases.
    rerank_weight_coverage: float = 0.45
    rerank_weight_vector: float = 0.35
    rerank_weight_title: float = 0.10
    rerank_weight_authority: float = 0.10
    # Two cross-document sentences about the same thing (content-word Jaccard >= this)
    # stating different quantities are treated as a potential contradiction.
    conflict_sentence_similarity: float = 0.4

    # --- Grounding / confidence ------------------------------------------------------
    # Share of an answer sentence's content words that must appear in its cited evidence.
    grounding_min_claim_support: float = 0.6
    grounding_llm_judge_enabled: bool = True
    confidence_high_threshold: float = 0.75
    confidence_medium_threshold: float = 0.55

    # --- Conversation / handoff ------------------------------------------------------
    conversation_history_messages: int = 6
    conversation_message_max_chars: int = 800
    conversation_summary_max_chars: int = 1_500
    max_user_message_chars: int = 4_000
    handoff_failed_answers_threshold: int = 2
    handoff_on_provider_failure: bool = True
    conversation_retention_days: int = 0  # 0 disables retention cleanup
    # A resolved conversation is closed automatically when the customer has not replied for this
    # many days (a customer reply reopens it and cancels the timer). 0 disables automatic closing.
    resolved_auto_close_days: int = 7
    # How often the worker runs background maintenance such as the automatic close.
    maintenance_interval_seconds: int = 3600
    # Starting a chat with the same opening question as a not-yet-closed conversation from this
    # many recent hours continues that conversation instead of creating a duplicate. 0 disables.
    conversation_reuse_window_hours: int = 24

    # --- Uploads / ingestion ---------------------------------------------------------
    max_upload_size: int = 20 * 1024 * 1024
    max_document_chars: int = 2_000_000
    max_document_pages: int = 500
    storage_provider: Literal["local", "s3"] = "local"
    storage_local_path: str = "./data/uploads"
    s3_bucket: str = ""
    s3_prefix: str = "documents/"
    s3_endpoint_url: str | None = None
    ingestion_worker_embedded: bool = True
    ingestion_poll_interval_seconds: float = 2.0
    ingestion_stale_after_seconds: int = 900
    ingestion_max_attempts: int = 3

    # --- Rate limits ("<count>/<second|minute|hour>") -------------------------------
    rate_limit_enabled: bool = True
    rate_limit_login: str = "10/minute"
    rate_limit_chat: str = "30/minute"
    rate_limit_upload: str = "30/hour"
    rate_limit_admin: str = "300/minute"

    # --- Seed (development only) -----------------------------------------------------
    seed_admin_password: SecretStr | None = None
    seed_agent_password: SecretStr | None = None
    seed_customer_password: SecretStr | None = None

    # Overall budget for one chat turn (retrieval + generation + validation).
    chat_timeout_seconds: float = 120.0

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, value: str) -> str:
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+asyncpg://", 1)
        return value

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        if self.app_env == "production":
            if len(self.jwt_secret.get_secret_value()) < 32:
                raise ValueError("JWT_SECRET must be at least 32 characters in production")
            if not self.auth_cookie_secure:
                raise ValueError("AUTH_COOKIE_SECURE must be true in production")
            if "*" in self.cors_origins:
                raise ValueError("CORS_ALLOWED_ORIGINS cannot contain '*' in production")
        elif not self.jwt_secret.get_secret_value():
            raise ValueError("JWT_SECRET is required (see .env.example)")
        if self.llm_provider == "openai" and not (self.openai_api_key and self.llm_model):
            raise ValueError("LLM_PROVIDER=openai requires OPENAI_API_KEY and LLM_MODEL")
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            raise ValueError("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY")
        if self.embedding_provider == "openai" and not self.openai_api_key:
            raise ValueError("EMBEDDING_PROVIDER=openai requires OPENAI_API_KEY")
        if self.storage_provider == "s3" and not self.s3_bucket:
            raise ValueError("STORAGE_PROVIDER=s3 requires S3_BUCKET")
        if not 1 <= self.vector_dimension <= 2000:
            raise ValueError("VECTOR_DIMENSION must be between 1 and 2000 (pgvector HNSW limit)")
        weights = (
            self.rerank_weight_coverage
            + self.rerank_weight_vector
            + self.rerank_weight_title
            + self.rerank_weight_authority
        )
        if abs(weights - 1.0) > 1e-6:
            raise ValueError("RERANK_WEIGHT_* values must sum to 1.0")
        if self.confidence_medium_threshold > self.confidence_high_threshold:
            raise ValueError("CONFIDENCE_MEDIUM_THRESHOLD must not exceed the HIGH threshold")
        self.authority_ranks()  # fail fast on a malformed ranking
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    @property
    def effective_llm_model(self) -> str:
        if self.llm_model:
            return self.llm_model
        return {"anthropic": "claude-opus-5", "none": "extractive-v1"}.get(self.llm_provider, "")

    def authority_ranks(self) -> dict[str, int]:
        ranks: dict[str, int] = {}
        for item in self.source_authority_ranking.split(","):
            name, _, value = item.partition(":")
            if not name.strip() or not value.strip().isdigit():
                raise ValueError(f"Malformed SOURCE_AUTHORITY_RANKING entry: {item!r}")
            ranks[name.strip()] = int(value)
        return ranks

    def authority_weight(self, authority: str) -> float:
        ranks = self.authority_ranks()
        top = max(ranks.values()) or 1
        return ranks.get(authority, 0) / top


@lru_cache
def get_settings() -> Settings:
    return Settings()

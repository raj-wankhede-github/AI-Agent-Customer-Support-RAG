"""initial schema

Revision ID: 80c59226898d
Revises:
Create Date: 2026-09-15 15:56:00

Creates the full MVP schema, the pgvector extension and per-dimension HNSW indexes.
Safe to run against an empty database; never drops data on upgrade.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "80c59226898d"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Partial HNSW indexes on embedding::vector(N) for common embedding sizes. Other sizes:
#   python -m app.cli ensure-vector-index
HNSW_DIMENSIONS = (384, 1024, 1536)


def _ts(name: str = "created_at") -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "companies",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        _ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_companies")),
        sa.UniqueConstraint("slug", name=op.f("uq_companies_slug")),
    )
    op.create_table(
        "revoked_tokens",
        sa.Column("jti", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("jti", name=op.f("pk_revoked_tokens")),
    )
    op.create_index(op.f("ix_revoked_tokens_expires_at"), "revoked_tokens", ["expires_at"])
    op.create_table(
        "users",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        _ts(),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_users_company_id_companies"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index("ix_users_company_role", "users", ["company_id", "role"])
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)
    op.create_table(
        "audit_logs",
        sa.Column("company_id", sa.UUID(), nullable=True),
        sa.Column("actor_user_id", sa.UUID(), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target_type", sa.String(length=50), nullable=True),
        sa.Column("target_id", sa.String(length=100), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        _ts(),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name=op.f("fk_audit_logs_actor_user_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_audit_logs_company_id_companies"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index("ix_audit_logs_company_created", "audit_logs", ["company_id", "created_at"])
    op.create_table(
        "conversations",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("handoff_status", sa.String(length=20), nullable=False),
        sa.Column("assigned_agent_id", sa.UUID(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summarized_message_count", sa.Integer(), nullable=False),
        sa.Column("consecutive_failed_answers", sa.Integer(), nullable=False),
        sa.Column("last_message_preview", sa.String(length=240), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        _ts("updated_at"),
        _ts(),
        sa.ForeignKeyConstraint(
            ["assigned_agent_id"],
            ["users.id"],
            name=op.f("fk_conversations_assigned_agent_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_conversations_company_id_companies"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_conversations_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
    )
    op.create_index("ix_conversations_company_status", "conversations", ["company_id", "status", "last_message_at"])
    op.create_index("ix_conversations_owner_recent", "conversations", ["company_id", "user_id", "last_message_at"])
    op.create_index(
        "ix_conversations_title_fts", "conversations", [sa.text("to_tsvector('simple', title)")], postgresql_using="gin"
    )
    op.create_table(
        "documents",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("source_type", sa.String(length=20), nullable=False),
        sa.Column("source_uri", sa.String(length=1000), nullable=True),
        sa.Column("authority", sa.String(length=40), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=True),
        sa.Column("product", sa.String(length=100), nullable=True),
        sa.Column("locale", sa.String(length=20), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("active_version_id", sa.UUID(), nullable=True),
        sa.Column("latest_version_number", sa.Integer(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        _ts("updated_at"),
        _ts(),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_documents_company_id_companies"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_documents_created_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
    )
    op.create_index("ix_documents_company_status", "documents", ["company_id", "status", "updated_at"])
    op.create_table(
        "document_versions",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=1000), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=True),
        sa.Column("extracted_title", sa.String(length=300), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        _ts(),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_document_versions_company_id_companies"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_document_versions_created_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_document_versions_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_versions")),
        sa.UniqueConstraint("document_id", "version_number", name="uq_document_versions_number"),
    )
    op.create_index("ix_document_versions_company_checksum", "document_versions", ["company_id", "checksum"])
    op.create_index("ix_document_versions_queue", "document_versions", ["status", "created_at"])
    # Circular reference documents <-> document_versions: added once both tables exist.
    op.create_foreign_key(
        op.f("fk_documents_active_version_id_document_versions"),
        "documents",
        "document_versions",
        ["active_version_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_table(
        "messages",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("author_user_id", sa.UUID(), nullable=True),
        sa.Column("client_message_id", sa.String(length=100), nullable=True),
        sa.Column("reply_to_message_id", sa.UUID(), nullable=True),
        sa.Column("answer_status", sa.String(length=30), nullable=True),
        sa.Column("confidence", sa.String(length=10), nullable=True),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("retrieval_metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("handoff_reason", sa.String(length=50), nullable=True),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("prompt_version", sa.String(length=200), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        _ts(),
        sa.ForeignKeyConstraint(
            ["author_user_id"], ["users.id"], name=op.f("fk_messages_author_user_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_messages_company_id_companies"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_messages_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reply_to_message_id"],
            ["messages.id"],
            name=op.f("fk_messages_reply_to_message_id_messages"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
        sa.UniqueConstraint("conversation_id", "client_message_id", name="uq_messages_idempotency"),
    )
    op.create_index("ix_messages_company_role_created", "messages", ["company_id", "role", "created_at"])
    op.create_index(
        "ix_messages_content_fts", "messages", [sa.text("to_tsvector('simple', content)")], postgresql_using="gin"
    )
    op.create_index("ix_messages_conversation_created", "messages", ["conversation_id", "created_at"])
    op.create_table(
        "document_chunks",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("document_version_id", sa.UUID(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("section_title", sa.String(length=500), nullable=True),
        sa.Column("heading_path", sa.String(length=1000), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "setweight(to_tsvector('english', coalesce(heading_path, '')), 'A') || "
                "setweight(to_tsvector('english', content), 'B')",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        _ts(),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_document_chunks_company_id_companies"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name=op.f("fk_document_chunks_document_id_documents"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name=op.f("fk_document_chunks_document_version_id_document_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_chunks")),
        sa.UniqueConstraint("document_version_id", "chunk_index", name="uq_document_chunks_index"),
    )
    op.create_index("ix_document_chunks_company_document", "document_chunks", ["company_id", "document_id"])
    op.create_index("ix_document_chunks_search", "document_chunks", ["search_vector"], postgresql_using="gin")
    op.create_table(
        "feedback",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("message_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("rating", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.String(length=30), nullable=True),
        sa.Column("comment", sa.String(length=1000), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        _ts("updated_at"),
        _ts(),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_feedback_company_id_companies"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_feedback_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["messages.id"], name=op.f("fk_feedback_message_id_messages"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_feedback_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_feedback")),
        sa.UniqueConstraint("message_id", "user_id", name="uq_feedback_message_user"),
    )
    op.create_index("ix_feedback_company_created", "feedback", ["company_id", "created_at"])
    op.create_table(
        "handoffs",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("triggering_message_id", sa.UUID(), nullable=True),
        sa.Column("reason_code", sa.String(length=50), nullable=False),
        sa.Column("reason_detail", sa.String(length=500), nullable=False),
        sa.Column("priority", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("assigned_agent_id", sa.UUID(), nullable=True),
        sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        _ts(),
        sa.ForeignKeyConstraint(
            ["assigned_agent_id"], ["users.id"], name=op.f("fk_handoffs_assigned_agent_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_handoffs_company_id_companies"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_handoffs_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["triggering_message_id"],
            ["messages.id"],
            name=op.f("fk_handoffs_triggering_message_id_messages"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_handoffs")),
    )
    op.create_index("ix_handoffs_conversation", "handoffs", ["conversation_id"])
    op.create_index("ix_handoffs_queue", "handoffs", ["company_id", "status", "created_at"])
    op.create_table(
        "rag_traces",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("conversation_id", sa.UUID(), nullable=False),
        sa.Column("user_message_id", sa.UUID(), nullable=False),
        sa.Column("assistant_message_id", sa.UUID(), nullable=True),
        sa.Column("original_query", sa.Text(), nullable=False),
        sa.Column("standalone_query", sa.Text(), nullable=False),
        sa.Column("analysis", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("candidates", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("selected_evidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("sufficiency", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("conflicts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("validation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("decision", sa.String(length=30), nullable=False),
        sa.Column("decision_reason", sa.String(length=500), nullable=True),
        sa.Column("handoff_reason", sa.String(length=50), nullable=True),
        sa.Column("timings_ms", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prompt_versions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        _ts(),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"],
            ["messages.id"],
            name=op.f("fk_rag_traces_assistant_message_id_messages"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_rag_traces_company_id_companies"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            name=op.f("fk_rag_traces_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_message_id"],
            ["messages.id"],
            name=op.f("fk_rag_traces_user_message_id_messages"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rag_traces")),
        sa.UniqueConstraint("assistant_message_id", name=op.f("uq_rag_traces_assistant_message_id")),
    )
    op.create_index("ix_rag_traces_company_created", "rag_traces", ["company_id", "created_at"])
    op.create_index("ix_rag_traces_conversation", "rag_traces", ["conversation_id"])
    op.create_table(
        "chunk_embeddings",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("chunk_id", sa.UUID(), nullable=False),
        sa.Column("document_version_id", sa.UUID(), nullable=False),
        sa.Column("embedding_model", sa.String(length=100), nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        _ts(),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["document_chunks.id"],
            name=op.f("fk_chunk_embeddings_chunk_id_document_chunks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_chunk_embeddings_company_id_companies"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name=op.f("fk_chunk_embeddings_document_version_id_document_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunk_embeddings")),
        sa.UniqueConstraint("chunk_id", "embedding_model", name="uq_chunk_embeddings_model"),
    )
    op.create_index("ix_chunk_embeddings_company_model", "chunk_embeddings", ["company_id", "embedding_model"])
    for dim in HNSW_DIMENSIONS:
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_chunk_embeddings_hnsw_{dim} ON chunk_embeddings "
            f"USING hnsw ((embedding::vector({dim})) vector_cosine_ops) WHERE dimension = {dim}"
        )


def downgrade() -> None:
    # Destructive: only ever run explicitly by an operator, never at application startup.
    for dim in HNSW_DIMENSIONS:
        op.execute(f"DROP INDEX IF EXISTS ix_chunk_embeddings_hnsw_{dim}")
    op.drop_table("chunk_embeddings")
    op.drop_table("rag_traces")
    op.drop_table("handoffs")
    op.drop_table("feedback")
    op.drop_table("document_chunks")
    op.drop_table("messages")
    op.drop_constraint(op.f("fk_documents_active_version_id_document_versions"), "documents", type_="foreignkey")
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.drop_table("conversations")
    op.drop_table("audit_logs")
    op.drop_table("users")
    op.drop_table("revoked_tokens")
    op.drop_table("companies")

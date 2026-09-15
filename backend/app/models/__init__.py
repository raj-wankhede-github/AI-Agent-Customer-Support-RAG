"""Import every model so Alembic and metadata see the complete schema."""

from app.models.audit import AuditLog, RevokedToken
from app.models.conversation import Conversation, Feedback, Handoff, Message, RagTrace
from app.models.document import ChunkEmbedding, Document, DocumentChunk, DocumentVersion
from app.models.tenant import Company, User

__all__ = [
    "AuditLog",
    "ChunkEmbedding",
    "Company",
    "Conversation",
    "Document",
    "DocumentChunk",
    "DocumentVersion",
    "Feedback",
    "Handoff",
    "Message",
    "RagTrace",
    "RevokedToken",
    "User",
]

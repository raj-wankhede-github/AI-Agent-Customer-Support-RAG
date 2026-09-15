"""conversation resolution lifecycle: who resolved, reopen tracking, automatic close

Revision ID: c7d2e8a1f4b6
Revises: b2f4c1d9e7a3
Create Date: 2026-09-15 19:30:00

Additive. Backfills resolved_at / resolved_by_user_id from `conversation.resolved` audit
entries; resolved conversations without an audit entry use updated_at so they become
eligible for automatic closing.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7d2e8a1f4b6"
down_revision: str | None = "b2f4c1d9e7a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "conversations", sa.Column("closed_automatically", sa.Boolean(), server_default="false", nullable=False)
    )
    op.add_column("conversations", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("conversations", sa.Column("resolved_by_user_id", sa.UUID(), nullable=True))
    op.add_column("conversations", sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("conversations", sa.Column("reopen_count", sa.Integer(), server_default="0", nullable=False))
    op.create_foreign_key(
        op.f("fk_conversations_resolved_by_user_id_users"),
        "conversations",
        "users",
        ["resolved_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_conversations_resolved_due",
        "conversations",
        ["resolved_at"],
        postgresql_where=sa.text("status = 'RESOLVED'"),
    )
    op.execute(
        """
        UPDATE conversations AS c
           SET resolved_at = a.created_at, resolved_by_user_id = a.actor_user_id
          FROM (
                SELECT DISTINCT ON (target_id) target_id, actor_user_id, created_at
                  FROM audit_logs
                 WHERE action = 'conversation.resolved'
                 ORDER BY target_id, created_at DESC
               ) AS a
         WHERE c.id::text = a.target_id
           AND c.status IN ('RESOLVED', 'CLOSED')
        """
    )
    op.execute("UPDATE conversations SET resolved_at = updated_at WHERE status = 'RESOLVED' AND resolved_at IS NULL")


def downgrade() -> None:
    op.drop_index("ix_conversations_resolved_due", table_name="conversations")
    op.drop_constraint(op.f("fk_conversations_resolved_by_user_id_users"), "conversations", type_="foreignkey")
    for column in ("reopen_count", "reopened_at", "resolved_by_user_id", "resolved_at", "closed_automatically"):
        op.drop_column("conversations", column)

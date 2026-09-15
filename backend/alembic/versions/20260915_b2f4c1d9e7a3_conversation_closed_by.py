"""record who closed a conversation

Revision ID: b2f4c1d9e7a3
Revises: 80c59226898d
Create Date: 2026-09-15 18:30:00

Adds conversations.closed_by_user_id and backfills it for already-closed conversations from
the `conversation.closed` audit-log entries. Additive and safe to run online.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2f4c1d9e7a3"
down_revision: str | None = "80c59226898d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("conversations", sa.Column("closed_by_user_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_conversations_closed_by_user_id_users"),
        "conversations",
        "users",
        ["closed_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE conversations AS c
           SET closed_by_user_id = a.actor_user_id
          FROM (
                SELECT DISTINCT ON (target_id) target_id, actor_user_id
                  FROM audit_logs
                 WHERE action = 'conversation.closed' AND actor_user_id IS NOT NULL
                 ORDER BY target_id, created_at DESC
               ) AS a
         WHERE c.id::text = a.target_id
           AND c.status = 'CLOSED'
           AND c.closed_by_user_id IS NULL
        """
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_conversations_closed_by_user_id_users"), "conversations", type_="foreignkey")
    op.drop_column("conversations", "closed_by_user_id")

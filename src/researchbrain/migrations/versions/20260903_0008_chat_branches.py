"""chat branches

Revision ID: 20260903_0008
Revises: 20260902_0007
Create Date: 2026-09-03 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0008"
down_revision: str | Sequence[str] | None = "20260902_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _string_column(name: str, length: int) -> sa.Column:
    return sa.Column(name, sa.String(length=length), nullable=False, server_default="")


def upgrade() -> None:
    with op.batch_alter_table("chat_sessions", schema=None) as batch_op:
        batch_op.add_column(_string_column("parent_session_id", 36))
        batch_op.add_column(_string_column("root_message_id", 36))
        batch_op.add_column(_string_column("branch_source_message_id", 36))
        batch_op.add_column(_string_column("branch_name", 300))
        batch_op.add_column(_string_column("branch_source", 100))
        batch_op.add_column(sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))

    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.add_column(_string_column("parent_message_id", 36))
        batch_op.create_index("ix_chat_message_parent", ["parent_message_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("chat_messages", schema=None) as batch_op:
        batch_op.drop_index("ix_chat_message_parent")
        batch_op.drop_column("parent_message_id")

    with op.batch_alter_table("chat_sessions", schema=None) as batch_op:
        batch_op.drop_column("archived_at")
        batch_op.drop_column("branch_source")
        batch_op.drop_column("branch_name")
        batch_op.drop_column("branch_source_message_id")
        batch_op.drop_column("root_message_id")
        batch_op.drop_column("parent_session_id")

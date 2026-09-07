"""persistent research follow-up queue

Revision ID: 20260903_0009
Revises: 20260903_0008
Create Date: 2026-09-03 00:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0009"
down_revision: str | Sequence[str] | None = "20260903_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_follow_ups",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_run_id", sa.String(length=36), nullable=False),
        sa.Column("source_message_id", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("target_session_id", sa.String(length=36), nullable=False),
        sa.Column("target_branch_id", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False, server_default="local"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("started_run_id", sa.String(length=36), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["source_run_id"], ["research_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_research_follow_up_target_queue",
        "research_follow_ups",
        ["target_session_id", "status", "position"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_research_follow_up_target_queue", table_name="research_follow_ups")
    op.drop_table("research_follow_ups")

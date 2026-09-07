"""persistent Agent turns, tool calls, and checkpoints

Revision ID: 20260903_0010
Revises: 20260903_0009
Create Date: 2026-09-03 01:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260903_0010"
down_revision: str | Sequence[str] | None = "20260903_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_turns",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False, server_default="agent"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="running"),
        sa.Column("action", sa.JSON(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("stop_decision", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_research_turn_sequence"),
    )
    op.create_index("ix_research_turn_run_status", "research_turns", ["run_id", "status"])

    op.create_table(
        "research_tool_calls",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("turn_id", sa.String(length=36), nullable=False),
        sa.Column("call_id", sa.String(length=80), nullable=False),
        sa.Column("tool_name", sa.String(length=100), nullable=False),
        sa.Column("arguments", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="running"),
        sa.Column("readonly", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("idempotency_key", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("error_code", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["turn_id"], ["research_turns.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "call_id", name="uq_research_tool_call_id"),
    )
    op.create_index("ix_research_tool_call_turn", "research_tool_calls", ["turn_id", "status"])
    op.create_index(
        "ix_research_tool_call_idempotency",
        "research_tool_calls",
        ["run_id", "idempotency_key"],
    )

    op.create_table(
        "research_checkpoints",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("turn_sequence", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("action", sa.JSON(), nullable=False),
        sa.Column("tool_results", sa.JSON(), nullable=False),
        sa.Column("coverage", sa.JSON(), nullable=False),
        sa.Column("budgets", sa.JSON(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("safe", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_research_checkpoint_sequence"),
    )
    op.create_index(
        "ix_research_checkpoint_run_turn",
        "research_checkpoints",
        ["run_id", "turn_sequence"],
    )


def downgrade() -> None:
    op.drop_index("ix_research_checkpoint_run_turn", table_name="research_checkpoints")
    op.drop_table("research_checkpoints")
    op.drop_index("ix_research_tool_call_idempotency", table_name="research_tool_calls")
    op.drop_index("ix_research_tool_call_turn", table_name="research_tool_calls")
    op.drop_table("research_tool_calls")
    op.drop_index("ix_research_turn_run_status", table_name="research_turns")
    op.drop_table("research_turns")

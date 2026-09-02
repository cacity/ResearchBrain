"""research orchestrator runs and evidence ledger

Revision ID: 20260902_0007
Revises: 20260817_0006
Create Date: 2026-09-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260902_0007"
down_revision: str | Sequence[str] | None = "20260817_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("user_message_id", sa.String(length=36), nullable=False),
        sa.Column("assistant_message_id", sa.String(length=36), nullable=True),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("phase", sa.String(length=40), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("plan", sa.JSON(), nullable=False),
        sa.Column("coverage", sa.JSON(), nullable=False),
        sa.Column("budgets", sa.JSON(), nullable=False),
        sa.Column("approvals", sa.JSON(), nullable=False),
        sa.Column("limitations", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["assistant_message_id"], ["chat_messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_message_id"], ["chat_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_research_run_session_created", "research_runs", ["session_id", "created_at"])
    op.create_index("ix_research_run_status", "research_runs", ["status", "updated_at"])

    op.create_table(
        "research_steps",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=40), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("input", sa.JSON(), nullable=False),
        sa.Column("output", sa.JSON(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=100), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_research_step_sequence"),
    )
    op.create_index("ix_research_step_run_phase", "research_steps", ["run_id", "phase"])

    op.create_table(
        "research_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_research_event_sequence"),
    )
    op.create_index("ix_research_event_run_sequence", "research_events", ["run_id", "sequence"])

    op.create_table(
        "research_evidence",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("evidence_id", sa.String(length=20), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_id", sa.String(length=200), nullable=False),
        sa.Column("source_kind", sa.String(length=30), nullable=False),
        sa.Column("source_name", sa.String(length=200), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("evidence_level", sa.String(length=40), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("section", sa.String(length=300), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=True),
        sa.Column("page_end", sa.Integer(), nullable=True),
        sa.Column("retrieval_query", sa.Text(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("discovery_record", sa.JSON(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("cited", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["research_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "evidence_fingerprint", name="uq_research_evidence_fingerprint"),
        sa.UniqueConstraint("run_id", "evidence_id", name="uq_research_evidence_id"),
    )
    op.create_index("ix_research_evidence_run_cited", "research_evidence", ["run_id", "cited"])

    op.create_table(
        "chat_session_memories",
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("through_message_id", sa.String(length=36), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id"),
    )


def downgrade() -> None:
    op.drop_table("chat_session_memories")
    op.drop_index("ix_research_evidence_run_cited", table_name="research_evidence")
    op.drop_table("research_evidence")
    op.drop_index("ix_research_event_run_sequence", table_name="research_events")
    op.drop_table("research_events")
    op.drop_index("ix_research_step_run_phase", table_name="research_steps")
    op.drop_table("research_steps")
    op.drop_index("ix_research_run_status", table_name="research_runs")
    op.drop_index("ix_research_run_session_created", table_name="research_runs")
    op.drop_table("research_runs")

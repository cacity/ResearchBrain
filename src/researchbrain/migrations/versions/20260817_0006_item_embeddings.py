"""track title and abstract embeddings

Revision ID: 20260817_0006
Revises: 20260817_0005
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0006"
down_revision: str | Sequence[str] | None = "20260817_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "item_embeddings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("embedding_provider", sa.String(length=100), nullable=False),
        sa.Column("embedding_model", sa.String(length=100), nullable=False),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=False),
        sa.Column("index_version", sa.String(length=50), nullable=False),
        sa.Column("index_status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "item_id",
            "embedding_model",
            "index_version",
            name="uq_item_embedding",
        ),
    )
    op.create_index(
        "ix_item_embedding_status",
        "item_embeddings",
        ["item_id", "index_status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_item_embedding_status", table_name="item_embeddings")
    op.drop_table("item_embeddings")

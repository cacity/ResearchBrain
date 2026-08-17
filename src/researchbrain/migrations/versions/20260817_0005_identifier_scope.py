"""scope identifier uniqueness to an item

Revision ID: 20260817_0005
Revises: 20260816_0004
Create Date: 2026-08-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260817_0005"
down_revision: str | Sequence[str] | None = "20260816_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("identifiers") as batch_op:
        batch_op.drop_constraint("uq_identifier_library", type_="unique")
        batch_op.create_unique_constraint(
            "uq_identifier_item",
            ["item_id", "scheme", "normalized_value"],
        )
        batch_op.create_index(
            "ix_identifier_library_lookup",
            ["library_id", "scheme", "normalized_value"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("identifiers") as batch_op:
        batch_op.drop_index("ix_identifier_library_lookup")
        batch_op.drop_constraint("uq_identifier_item", type_="unique")
        batch_op.create_unique_constraint(
            "uq_identifier_library",
            ["library_id", "scheme", "normalized_value"],
        )

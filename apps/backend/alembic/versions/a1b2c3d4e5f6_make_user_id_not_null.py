"""make_user_id_not_null_add_master_index

Revision ID: a1b2c3d4e5f6
Revises: 42511b93573f
Create Date: 2026-04-03 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '42511b93573f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Make user_id NOT NULL on resumes, jobs, improvements; add per-user master index."""
    # Delete only orphaned rows (NULL user_id) -- safe for populated databases
    op.execute(sa.text("DELETE FROM improvements WHERE user_id IS NULL"))
    op.execute(sa.text("DELETE FROM jobs WHERE user_id IS NULL"))
    op.execute(sa.text("DELETE FROM resumes WHERE user_id IS NULL"))

    # Make user_id NOT NULL using batch mode (required for SQLite).
    with op.batch_alter_table("resumes") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(36), nullable=False)

    with op.batch_alter_table("jobs") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(36), nullable=False)

    with op.batch_alter_table("improvements") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(36), nullable=False)

    # Partial unique index: one master resume per user.
    op.create_index(
        "ix_resumes_user_master",
        "resumes",
        ["user_id"],
        unique=True,
        sqlite_where=sa.text("is_master = 1"),
        postgresql_where=sa.text("is_master = true"),
    )


def downgrade() -> None:
    """Revert user_id to nullable, drop master index."""
    op.drop_index("ix_resumes_user_master", table_name="resumes")

    with op.batch_alter_table("improvements") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(36), nullable=True)

    with op.batch_alter_table("jobs") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(36), nullable=True)

    with op.batch_alter_table("resumes") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.String(36), nullable=True)

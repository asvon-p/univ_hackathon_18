"""durable file cleanup after garment deletion

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "storage_deletion_jobs",
        sa.Column("garment_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(100), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        sa.PrimaryKeyConstraint("garment_id"),
    )
    op.create_index("ix_storage_deletion_jobs_available_at", "storage_deletion_jobs", ["available_at"])


def downgrade() -> None:
    op.drop_table("storage_deletion_jobs")

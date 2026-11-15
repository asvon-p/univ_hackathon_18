"""Shared bounded try-on admission; existing images/results are untouched."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "tryon_queue_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("capacity", sa.Integer(), nullable=False, server_default="8"),
        sa.CheckConstraint("id = 1 AND capacity BETWEEN 1 AND 8", name="tryon_capacity"),
    )
    op.execute("INSERT INTO tryon_queue_state (id, capacity) VALUES (1, 8)")
    op.create_table(
        "tryon_admissions",
        sa.Column("job_id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
        ),
        sa.Column("session_key", sa.String(64)),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_tryon_admissions_expires_at", "tryon_admissions", ["expires_at"])


def downgrade():
    op.drop_table("tryon_admissions")
    op.drop_table("tryon_queue_state")

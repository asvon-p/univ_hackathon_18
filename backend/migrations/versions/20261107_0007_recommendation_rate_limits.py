"""persistent per-user recommendation rate limits

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recommendation_rate_limits",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "request_times",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            server_default="[]",
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("recommendation_rate_limits")

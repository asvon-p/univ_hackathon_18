"""Owned day plans and recommendation explanations; preserve all existing records."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017"
down_revision = "0016"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "day_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("events", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("version >= 1", name=op.f("ck_day_plans_version_positive")),
    )
    op.create_index("ix_day_plans_user_id", "day_plans", ["user_id"])
    op.add_column(
        "recommendation_outfits",
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default="{}"),
    )


def downgrade():
    op.drop_column("recommendation_outfits", "details")
    op.drop_table("day_plans")

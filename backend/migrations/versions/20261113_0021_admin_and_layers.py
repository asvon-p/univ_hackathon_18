"""Explicit administrator grants, usage receipts, and editable outerwear openings."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021"
down_revision = "0020"
branch_labels = depends_on = None


def upgrade():
    op.add_column("users", sa.Column("is_admin", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column(
        "garments", sa.Column("opening_polygon", postgresql.JSONB(), nullable=False, server_default="[]")
    )
    op.create_table(
        "api_usage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("cached_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("estimated_usd", sa.Float()),
        sa.Column("price_basis", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_api_usage_user_id", "api_usage", ["user_id"])


def downgrade():
    op.drop_table("api_usage")
    op.drop_column("garments", "opening_polygon")
    op.drop_column("users", "is_admin")

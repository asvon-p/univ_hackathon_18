"""stage 3: recommendations and ranked outfits

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "recommendations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("context", postgresql.JSONB(), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("latency_ms >= 0", name="latency_nonnegative"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recommendations_user_id", "recommendations", ["user_id"])
    op.create_index("ix_recommendations_created_at", "recommendations", ["created_at"])
    op.create_table(
        "recommendation_outfits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recommendation_id", sa.Uuid(), nullable=False),
        sa.Column("rank", sa.SmallInteger(), nullable=False),
        sa.Column("top_id", sa.Uuid(), nullable=False),
        sa.Column("bottom_id", sa.Uuid(), nullable=False),
        sa.Column("shoes_id", sa.Uuid(), nullable=False),
        sa.Column("outerwear_id", sa.Uuid()),
        sa.Column("style_tags", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("score", sa.Float()),
        sa.CheckConstraint("rank BETWEEN 1 AND 3", name="rank_range"),
        sa.UniqueConstraint(
            "recommendation_id", "rank", name="uq_recommendation_outfits_recommendation_rank"
        ),
        sa.ForeignKeyConstraint(["recommendation_id"], ["recommendations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["top_id"], ["garments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["bottom_id"], ["garments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shoes_id"], ["garments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["outerwear_id"], ["garments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("recommendation_id", "top_id", "bottom_id", "shoes_id", "outerwear_id"):
        op.create_index(f"ix_recommendation_outfits_{column}", "recommendation_outfits", [column])


def downgrade() -> None:
    op.drop_table("recommendation_outfits")
    op.drop_table("recommendations")

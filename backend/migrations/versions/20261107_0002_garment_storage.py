"""stage 1: users, garments, garment_attributes

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("nickname", sa.String(50), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "garments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(20), server_default="available", nullable=False),
        sa.Column("analysis_status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("original_path", sa.String(500), nullable=False),
        sa.Column("cutout_path", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('available', 'laundry', 'inactive')", name="status_values"),
        sa.CheckConstraint("analysis_status IN ('pending', 'done', 'failed')", name="analysis_status_values"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_garments_user_id", "garments", ["user_id"])
    op.create_table(
        "garment_attributes",
        sa.Column("garment_id", sa.UUID(), nullable=False),
        sa.Column("category", sa.String(20)),
        sa.Column("subcategory", sa.String(50)),
        sa.Column("color", sa.String(20), server_default="unknown", nullable=False),
        sa.Column("pattern", sa.String(20), server_default="unknown", nullable=False),
        sa.Column("style_candidates", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("thermal_level", sa.SmallInteger()),
        sa.Column("source", sa.String(10), server_default="model", nullable=False),
        sa.Column("model_version", sa.String(50)),
        sa.Column("raw_output", postgresql.JSONB()),
        sa.Column("error", sa.Text()),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("category IN ('top', 'bottom', 'outerwear', 'shoes')", name="category_values"),
        sa.CheckConstraint("thermal_level BETWEEN 1 AND 5", name="thermal_level_range"),
        sa.CheckConstraint("source IN ('model', 'user')", name="source_values"),
        sa.ForeignKeyConstraint(["garment_id"], ["garments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("garment_id"),
    )
    op.create_index("ix_garment_attributes_category", "garment_attributes", ["category"])


def downgrade() -> None:
    op.drop_table("garment_attributes")
    op.drop_table("garments")
    op.drop_table("users")

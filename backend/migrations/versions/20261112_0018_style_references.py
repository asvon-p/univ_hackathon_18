"""Private reference images, confirmed features and durable deletion; existing data unchanged."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0018"
down_revision = "0017"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "style_references",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(10), nullable=False, server_default="draft"),
        sa.Column("image_path", sa.String(500), nullable=False),
        sa.Column("features", JSONB(), nullable=False, server_default="[]"),
        sa.Column("regions", JSONB(), nullable=False, server_default="[]"),
        sa.Column("notes", JSONB(), nullable=False, server_default="[]"),
        sa.Column("focus", sa.String(20), nullable=False, server_default="balanced"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("version >= 1", name=op.f("ck_style_references_version_positive")),
        sa.CheckConstraint("status IN ('draft','ready')", name=op.f("ck_style_references_status_values")),
    )
    op.create_index("ix_style_references_user_id", "style_references", ["user_id"])
    op.create_table(
        "reference_analysis_usage",
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("requests", JSONB(), nullable=False),
    )
    op.create_table(
        "reference_deletion_jobs",
        sa.Column("reference_id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(100)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_reference_deletion_jobs_attempts_nonnegative")),
    )
    op.create_index("ix_reference_deletion_jobs_available_at", "reference_deletion_jobs", ["available_at"])


def downgrade():
    op.drop_table("reference_analysis_usage")
    op.drop_table("reference_deletion_jobs")
    op.drop_table("style_references")

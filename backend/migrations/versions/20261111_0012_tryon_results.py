"""Durable, private try-on results. Existing accounts and photos stay unchanged."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = depends_on = None


def upgrade():
    op.create_table("tryon_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("photo_id", sa.Uuid(), nullable=False),
        sa.Column("photo_version", sa.String(64), nullable=False),
        sa.Column("items", postgresql.JSONB(), nullable=False),
        sa.Column("names", postgresql.JSONB(), nullable=False),
        sa.Column("image_id", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_tryon_results_user_id", "tryon_results", ["user_id"])
    op.create_table("tryon_deletion_jobs",
        sa.Column("image_id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(100)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.create_index("ix_tryon_deletion_jobs_available_at", "tryon_deletion_jobs", ["available_at"])


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM tryon_results) OR EXISTS (SELECT 1 FROM tryon_deletion_jobs)")):
        raise RuntimeError("Export/clean try-on results before downgrade")
    op.drop_table("tryon_results")
    op.drop_table("tryon_deletion_jobs")

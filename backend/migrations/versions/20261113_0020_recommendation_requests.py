"""Durable per-user recommendation idempotency. Existing recommendations remain unchanged."""

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = depends_on = None


def upgrade():
    op.create_table(
        "recommendation_requests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("automatic", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(12), nullable=False, server_default="pending"),
        sa.Column("recommendation_id", sa.Uuid(), sa.ForeignKey("recommendations.id", ondelete="SET NULL")),
        sa.Column("error_status", sa.Integer()),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_detail", sa.String(500)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "key_hash", name="uq_recommendation_requests_user_key"),
        sa.CheckConstraint(
            "status IN ('pending','completed','failed')",
            name=op.f("ck_recommendation_requests_status_values"),
        ),
    )
    op.create_index(
        "ix_recommendation_requests_auto", "recommendation_requests", ["user_id", "fingerprint", "created_at"]
    )


def downgrade():
    op.drop_table("recommendation_requests")

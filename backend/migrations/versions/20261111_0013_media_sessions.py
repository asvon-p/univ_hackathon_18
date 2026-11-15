"""Account photo retention policy and sessions; never store customer image data."""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = depends_on = None

def upgrade():
    op.add_column("users", sa.Column("photo_policy", sa.String(20), nullable=False, server_default="persistent"))
    op.create_table("media_sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_media_sessions_user_id", "media_sessions", ["user_id"])
    op.create_index("ix_media_sessions_expires_at", "media_sessions", ["expires_at"])

def downgrade():
    op.drop_table("media_sessions")
    op.drop_column("users", "photo_policy")

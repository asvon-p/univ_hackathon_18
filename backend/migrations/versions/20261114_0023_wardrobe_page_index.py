"""Index wardrobe keyset pagination; preserve all rows and media."""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = depends_on = None


def upgrade():
    op.create_index("ix_garments_user_created_id", "garments", ["user_id", "created_at", "id"])


def downgrade():
    op.drop_index("ix_garments_user_created_id", table_name="garments")

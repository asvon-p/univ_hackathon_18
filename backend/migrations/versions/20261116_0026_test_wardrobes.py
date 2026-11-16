"""Remember one-time example provisioning without changing existing clothes."""
from alembic import op
import sqlalchemy as sa

revision = "0026"
down_revision = "0025"
branch_labels = depends_on = None


def upgrade():
    op.add_column("users", sa.Column("starter_wardrobe_provisioned", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE users SET starter_wardrobe_provisioned = true WHERE EXISTS (SELECT 1 FROM garments WHERE garments.user_id = users.id AND garments.is_example = true)")
    op.create_index("ix_garments_user_wardrobe_page", "garments", ["user_id", "is_example", "created_at", "id"])


def downgrade():
    op.drop_index("ix_garments_user_wardrobe_page", table_name="garments")
    op.drop_column("users", "starter_wardrobe_provisioned")

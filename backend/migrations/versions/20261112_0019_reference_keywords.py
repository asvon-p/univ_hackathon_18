"""Keep reference keywords; expire legacy originals without changing reference IDs or versions."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = depends_on = None


def upgrade():
    op.alter_column("style_references", "image_path", existing_type=sa.String(500), nullable=True)
    op.add_column("style_references", sa.Column("image_expires_at", sa.DateTime(timezone=True)))
    op.create_index("ix_style_references_image_expires_at", "style_references", ["image_expires_at"])
    # Files are removed by the durable cleanup worker, never inside a DB migration.
    op.execute(
        "UPDATE style_references SET image_expires_at = CURRENT_TIMESTAMP WHERE image_path IS NOT NULL"
    )


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM style_references WHERE image_path IS NULL"))
        .scalar()
    ):
        raise RuntimeError("Cannot restore deleted reference images; export/cleanup keyword records first")
    op.drop_index("ix_style_references_image_expires_at", table_name="style_references")
    op.drop_column("style_references", "image_expires_at")
    op.alter_column("style_references", "image_path", existing_type=sa.String(500), nullable=False)

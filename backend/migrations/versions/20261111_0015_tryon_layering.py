"""Version new renders without rewriting or removing existing images/results."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "tryon_results", sa.Column("render_version", sa.String(32), nullable=False, server_default="legacy")
    )
    op.add_column(
        "tryon_results",
        sa.Column("layering_status", sa.String(20), nullable=False, server_default="not_requested"),
    )


def downgrade():
    op.drop_column("tryon_results", "layering_status")
    op.drop_column("tryon_results", "render_version")

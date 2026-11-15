"""Automatic outerwear opening by default; preserve all existing manual polygons."""

from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = depends_on = None


def upgrade():
    op.add_column("garments", sa.Column("opening_mode", sa.String(12), nullable=False, server_default="auto"))
    op.execute("UPDATE garments SET opening_mode='manual' WHERE jsonb_array_length(opening_polygon)>0")


def downgrade():
    op.drop_column("garments", "opening_mode")

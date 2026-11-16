"""Add secondary colors and explicit feedback categories without rewriting existing data."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0025"
down_revision = "0024"
branch_labels = depends_on = None


def upgrade():
    for table, column in (("garment_attributes", "secondary_colors"), ("feedback_events", "reason_codes")):
        op.add_column(
            table,
            sa.Column(
                column, sa.JSON().with_variant(JSONB(), "postgresql"), nullable=False, server_default="[]"
            ),
        )


def downgrade():
    op.drop_column("feedback_events", "reason_codes")
    op.drop_column("garment_attributes", "secondary_colors")

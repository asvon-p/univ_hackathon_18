"""Identify starter garments; existing wardrobes are unchanged."""
from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = depends_on = None


def upgrade():
    op.add_column('garments', sa.Column('is_example', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('garments', 'is_example')

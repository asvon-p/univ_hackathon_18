"""Persist new photos after consent and track wears between washes."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = depends_on = None


def upgrade():
    op.add_column(
        "users", sa.Column("auth_token_version", sa.SmallInteger(), nullable=False, server_default="0")
    )
    # Old session tokens, including logged-out tokens, must not gain durable photo access.
    op.execute(
        "UPDATE users SET photo_policy = 'persistent', auth_token_version = 1 WHERE photo_policy = 'session'"
    )
    op.execute(
        "DELETE FROM media_sessions WHERE user_id IN (SELECT id FROM users WHERE auth_token_version = 1)"
    )
    op.add_column(
        "garments", sa.Column("wears_before_laundry", sa.SmallInteger(), nullable=False, server_default="1")
    )
    op.add_column("garments", sa.Column("wear_count", sa.SmallInteger(), nullable=False, server_default="0"))
    op.create_check_constraint(
        op.f("ck_garments_wears_before_laundry_range"), "garments", "wears_before_laundry BETWEEN 1 AND 2"
    )
    op.create_check_constraint(op.f("ck_garments_wear_count_range"), "garments", "wear_count BETWEEN 0 AND 2")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT EXISTS (SELECT 1 FROM users WHERE auth_token_version > 0)")):
        raise RuntimeError("Downgrade would reactivate retired session tokens")
    op.drop_constraint(op.f("ck_garments_wear_count_range"), "garments", type_="check")
    op.drop_constraint(op.f("ck_garments_wears_before_laundry_range"), "garments", type_="check")
    op.drop_column("garments", "wear_count")
    op.drop_column("garments", "wears_before_laundry")
    op.drop_column("users", "auth_token_version")

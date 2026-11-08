"""Three-slot recommendations and frontend metadata; preserve all legacy rows.

Revision ID: 0010
Revises: 0009
"""

from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("recommendation_outfits", "shoes_id", nullable=True)
    op.add_column("garments", sa.Column("name", sa.String(100), nullable=True))
    op.add_column("garments", sa.Column("location", sa.String(100), nullable=True))
    op.alter_column("user_profiles", "height_cm", type_=sa.Float(), existing_type=sa.SmallInteger())
    op.alter_column("user_profiles", "weight_kg", type_=sa.Float(), existing_type=sa.SmallInteger())
    op.add_column("user_profiles", sa.Column("age", sa.SmallInteger(), nullable=True))
    op.create_check_constraint("age_range", "user_profiles", "age BETWEEN 0 AND 130")
    op.drop_constraint(op.f("ck_user_profiles_body_info_consent"), "user_profiles", type_="check")
    op.create_check_constraint(
        "body_info_consent", "user_profiles",
        "consent_body_info_at IS NOT NULL OR (gender IS NULL AND height_cm IS NULL "
        "AND weight_kg IS NULL AND birth_year IS NULL AND age IS NULL AND avatar_config IS NULL)",
    )


def downgrade():
    # Never invent shoes or silently remove newly saved recommendations to satisfy the old schema.
    connection = op.get_bind()
    if connection.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM recommendation_outfits WHERE shoes_id IS NULL)")):
        raise RuntimeError("0010 downgrade requires an explicit data export/rollback plan: shoeless outfits exist")
    if connection.scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM user_profiles WHERE height_cm <> trunc(height_cm::numeric) "
        "OR weight_kg <> trunc(weight_kg::numeric) OR age IS NOT NULL) "
        "OR EXISTS (SELECT 1 FROM garments WHERE name IS NOT NULL OR location IS NOT NULL)"
    )):
        raise RuntimeError("0010 downgrade would lose frontend profile/garment metadata; export it first")
    op.drop_constraint(op.f("ck_user_profiles_body_info_consent"), "user_profiles", type_="check")
    op.create_check_constraint(
        "body_info_consent", "user_profiles",
        "consent_body_info_at IS NOT NULL OR (gender IS NULL AND height_cm IS NULL "
        "AND weight_kg IS NULL AND birth_year IS NULL AND avatar_config IS NULL)",
    )
    op.drop_constraint(op.f("ck_user_profiles_age_range"), "user_profiles", type_="check")
    op.drop_column("user_profiles", "age")
    for column in ("height_cm", "weight_kg"):
        op.alter_column("user_profiles", column, type_=sa.SmallInteger(), existing_type=sa.Float())
    op.drop_column("garments", "location")
    op.drop_column("garments", "name")
    op.alter_column("recommendation_outfits", "shoes_id", nullable=False)

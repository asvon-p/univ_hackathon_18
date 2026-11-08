"""Private versioned body photos and durable cleanup. Existing rows are preserved."""

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

OLD_CONSENT = (
    "consent_body_info_at IS NOT NULL OR (gender IS NULL AND height_cm IS NULL "
    "AND weight_kg IS NULL AND birth_year IS NULL AND age IS NULL AND avatar_config IS NULL)"
)


def upgrade():
    op.add_column("user_profiles", sa.Column("body_photo_id", sa.Uuid(), nullable=True))
    op.add_column("user_profiles", sa.Column("body_photo_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint(op.f("ck_user_profiles_body_info_consent"), "user_profiles", type_="check")
    op.create_check_constraint(
        "body_info_consent", "user_profiles", OLD_CONSENT[:-1] + " AND body_photo_id IS NULL)",
    )
    op.create_check_constraint(
        "body_photo_pair", "user_profiles",
        "(body_photo_id IS NULL AND body_photo_updated_at IS NULL) OR "
        "(body_photo_id IS NOT NULL AND body_photo_updated_at IS NOT NULL)",
    )
    op.create_table(
        "body_photo_deletion_jobs",
        sa.Column("photo_id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.String(100)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
    )
    op.create_index("ix_body_photo_deletion_jobs_available_at", "body_photo_deletion_jobs", ["available_at"])


def downgrade():
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS(SELECT 1 FROM user_profiles WHERE body_photo_id IS NOT NULL) "
        "OR EXISTS(SELECT 1 FROM body_photo_deletion_jobs)"
    )):
        raise RuntimeError("0011 downgrade requires body photo export/cleanup first")
    op.drop_table("body_photo_deletion_jobs")
    op.drop_constraint(op.f("ck_user_profiles_body_photo_pair"), "user_profiles", type_="check")
    op.drop_constraint(op.f("ck_user_profiles_body_info_consent"), "user_profiles", type_="check")
    op.create_check_constraint("body_info_consent", "user_profiles", OLD_CONSENT)
    op.drop_column("user_profiles", "body_photo_updated_at")
    op.drop_column("user_profiles", "body_photo_id")

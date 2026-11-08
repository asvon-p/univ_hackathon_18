"""stage 2: user profiles and disable legacy development login

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_profiles",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("gender", sa.String(10)),
        sa.Column("height_cm", sa.SmallInteger()),
        sa.Column("weight_kg", sa.SmallInteger()),
        sa.Column("birth_year", sa.SmallInteger()),
        sa.Column("preferred_styles", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("disliked_colors", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("consent_body_info_at", sa.DateTime(timezone=True)),
        sa.Column("avatar_config", postgresql.JSONB(none_as_null=True)),
        sa.CheckConstraint("gender IN ('male', 'female', 'other')", name="gender_values"),
        sa.CheckConstraint("height_cm BETWEEN 50 AND 250", name="height_range"),
        sa.CheckConstraint("weight_kg BETWEEN 20 AND 400", name="weight_range"),
        sa.CheckConstraint("birth_year BETWEEN 1900 AND 2100", name="birth_year_range"),
        sa.CheckConstraint(
            "consent_body_info_at IS NOT NULL OR (gender IS NULL AND height_cm IS NULL "
            "AND weight_kg IS NULL AND birth_year IS NULL AND avatar_config IS NULL)",
            name="body_info_consent",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.execute("INSERT INTO user_profiles (user_id, preferred_styles, disliked_colors) SELECT id, '{}', '{}' FROM users")
    # 기존 의류와 사용자 데이터를 보존하고 임시 계정의 로그인만 차단합니다.
    op.execute("UPDATE users SET is_active = false WHERE password_hash = '!disabled-stage1'")


def downgrade() -> None:
    op.drop_table("user_profiles")
    # 비활성화한 임시 계정은 되돌리더라도 자동으로 활성화하지 않습니다.

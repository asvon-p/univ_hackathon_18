"""stage 4: append-only feedback events

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feedback_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("recommendation_id", sa.Uuid(), nullable=True),
        sa.Column("outfit_id", sa.Uuid(), nullable=True),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(reason) <= 1000", name="reason_length"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recommendation_id"], ["recommendations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["outfit_id"], ["recommendation_outfits.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("user_id", "recommendation_id", "outfit_id", "created_at"):
        op.create_index(f"ix_feedback_events_{column}", "feedback_events", [column])
    # FK만 끊으면 자유 입력 사유에 개인정보가 남을 수 있으므로 함께 비웁니다.
    # 현재 스키마를 고정해 격리된 테스트 스키마/운영 스키마 밖을 수정하지 않습니다.
    op.execute("""
        CREATE FUNCTION anonymize_feedback_on_user_delete() RETURNS trigger
        LANGUAGE plpgsql SET search_path FROM CURRENT AS $$
        BEGIN
            UPDATE feedback_events
            SET user_id = NULL, recommendation_id = NULL, outfit_id = NULL, reason = NULL
            WHERE user_id = OLD.id;
            RETURN OLD;
        END;
        $$
    """)
    op.execute("""
        CREATE TRIGGER anonymize_feedback_on_user_delete BEFORE DELETE ON users
        FOR EACH ROW EXECUTE FUNCTION anonymize_feedback_on_user_delete()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER anonymize_feedback_on_user_delete ON users")
    op.execute("DROP FUNCTION anonymize_feedback_on_user_delete()")
    op.drop_table("feedback_events")

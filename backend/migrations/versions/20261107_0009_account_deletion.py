"""account file cleanup and fresh demo identities after withdrawal

Revision ID: 0009
Revises: 0008
"""

from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "account_storage_deletion_jobs",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(100), nullable=True),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_index(
        "ix_account_storage_deletion_jobs_available_at", "account_storage_deletion_jobs", ["available_at"]
    )
    op.add_column("users", sa.Column("is_demo", sa.Boolean(), nullable=False, server_default="false"))
    # 기존 seed 계정은 보존하고, 이후 탈퇴→seed 재생성에는 새 UUID를 발급합니다.
    users = sa.table("users", sa.column("id", sa.Uuid()), sa.column("email"), sa.column("is_demo"))
    for user_id, email in op.get_bind().execute(sa.select(users.c.id, users.c.email)):
        if user_id == uuid5(NAMESPACE_URL, f"fitjang:demo:v1:{email}"):
            op.get_bind().execute(users.update().where(users.c.id == user_id).values(is_demo=True))


def downgrade() -> None:
    op.drop_column("users", "is_demo")
    op.drop_table("account_storage_deletion_jobs")

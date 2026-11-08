"""init: pgvector 확장 활성화 (설치돼 있을 때만)

Revision ID: 0001
Revises:
Create Date: 2026-11-07
"""
from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # docker(pgvector 이미지)는 항상 있음. 로컬 설치 PostgreSQL 은 없을 수 있으므로 조건부.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
                CREATE EXTENSION IF NOT EXISTS vector;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    pass

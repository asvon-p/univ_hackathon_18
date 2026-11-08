"""preserve manual garment attributes during reanalysis

Revision ID: 0008
Revises: 0007
"""

import json

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "garment_attributes",
        sa.Column(
            "user_edited_fields",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            server_default="[]",
            nullable=False,
        ),
    )
    # 이전 데이터는 수정 필드를 알 수 없으므로 모든 속성을 보수적으로 보호합니다.
    # category=null은 PATCH에서 허용하지 않았으므로 모델이 채울 수 있도록 남깁니다.
    fields = ["category", "color", "pattern", "style_candidates", "subcategory", "thermal_level"]
    table = sa.table(
        "garment_attributes",
        sa.column("source"),
        sa.column("category"),
        sa.column("user_edited_fields", sa.JSON()),
    )
    for category_missing in (False, True):
        protected = fields[1:] if category_missing else fields
        op.execute(
            table.update()
            .where(table.c.source == "user")
            .where(table.c.category.is_(None) if category_missing else table.c.category.is_not(None))
            .values(user_edited_fields=op.inline_literal(json.dumps(protected)))
        )


def downgrade() -> None:
    op.drop_column("garment_attributes", "user_edited_fields")

"""쓰기 작업은 부모 사용자부터 잠가 탈퇴의 FK cascade와 잠금 순서를 맞춥니다."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.core.errors import AppError, ErrorCode
from backend.app.db.models import User


def require_active_user(db: Session, user_id: UUID, *, exclusive: bool = False) -> User:
    user = db.scalar(
        select(User)
        .where(User.id == user_id, User.is_active.is_(True))
        .with_for_update(read=not exclusive, key_share=not exclusive)
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise AppError(401, ErrorCode.AUTH_INVALID_TOKEN, "유효하지 않은 인증 토큰입니다")
    return user

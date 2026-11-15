"""라우터에서 쓰는 의존성 모음. 라우터는 구현이 아니라 여기서 꺼내 씁니다."""

from uuid import UUID

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.app.adapters.registry import get_recommender, get_vision, get_weather  # noqa: F401
from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import AppError, ErrorCode
from backend.app.core.security import decode_access_token
from backend.app.db.models import User
from backend.app.db.session import get_db
from backend.app.repositories.users import UserRepository
from backend.app.storage.local import get_storage  # noqa: F401

bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    if credentials is None:
        raise AppError(401, ErrorCode.AUTH_REQUIRED, "로그인이 필요합니다")
    user_id, token_version = decode_access_token(credentials.credentials, settings)
    user = UserRepository(db).get(user_id)
    if user is None or not user.is_active:
        raise AppError(401, ErrorCode.AUTH_INVALID_TOKEN, "유효하지 않은 인증 토큰입니다")
    if token_version != user.auth_token_version:
        raise AppError(401, ErrorCode.AUTH_TOKEN_EXPIRED, "다시 로그인해 주세요")
    if user.photo_policy == "session":
        from backend.app.services.media_session import token_hash, validate
        from backend.app.services.user_lock import require_active_user

        user = require_active_user(db, user_id, exclusive=True)
        key = token_hash(credentials.credentials)
        validate(db, user_id, key)
        user._photo_session_key = key
    return user


def get_current_user_id(user: User = Depends(get_current_user)) -> UUID:
    return user.id

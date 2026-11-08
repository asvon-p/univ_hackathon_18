"""bcrypt 비밀번호와 HS256 액세스 토큰. 토큰에는 사용자 ID만 식별정보로 담습니다."""

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from uuid import UUID

import bcrypt
import jwt

from backend.app.core.config import Settings
from backend.app.core.errors import AppError, ErrorCode


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, UnicodeError):
        # 단계 1의 !disabled-stage1 계정도 로그인할 수 없습니다.
        return False


@lru_cache(maxsize=1)
def dummy_password_hash() -> str:
    return hash_password("not-an-account-password")


def create_access_token(user_id: UUID, settings: Settings) -> str:
    settings.validate_auth()
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(minutes=settings.JWT_EXPIRE_MINUTES),
            "type": "access",
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )


def decode_access_token(token: str, settings: Settings) -> UUID:
    settings.validate_auth()
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=["HS256"],
            options={"require": ["sub", "iat", "exp", "type"]},
        )
        if payload["type"] != "access" or type(payload["iat"]) is not int or type(payload["exp"]) is not int:
            raise ValueError("invalid claims")
        return UUID(payload["sub"])
    except jwt.ExpiredSignatureError as exc:
        raise AppError(401, ErrorCode.AUTH_TOKEN_EXPIRED, "로그인이 만료되었습니다") from exc
    except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError) as exc:
        raise AppError(401, ErrorCode.AUTH_INVALID_TOKEN, "유효하지 않은 인증 토큰입니다") from exc

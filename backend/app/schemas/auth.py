from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr = Field(max_length=255)
    password: SecretStr = Field(min_length=1, max_length=72)

    @field_validator("email", mode="before")
    @classmethod
    def normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("password")
    @classmethod
    def bcrypt_byte_limit(cls, value: SecretStr):
        if len(value.get_secret_value().encode("utf-8")) > 72:
            raise ValueError("비밀번호는 UTF-8 기준 72바이트 이하여야 합니다")
        return value


class SignupRequest(LoginRequest):
    password: SecretStr = Field(min_length=8, max_length=72)
    nickname: str = Field(default="사용자", min_length=1, max_length=50)
    invite_code: SecretStr | None = Field(default=None, max_length=255)

    @field_validator("nickname", mode="before")
    @classmethod
    def strip_nickname(cls, value):
        return value.strip() if isinstance(value, str) else value


class AccountDeletionRequest(BaseModel):
    """본문 생략 또는 {}. 사용자 ID 등 삭제 대상을 클라이언트가 지정할 수 없습니다."""

    model_config = ConfigDict(extra="forbid")


class UserView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    nickname: str
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def utc_timestamp(cls, value: datetime):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class SignupResponse(UserView, TokenResponse):
    """기존 사용자 정보와 D의 가입 직후 로그인용 토큰을 함께 반환합니다."""

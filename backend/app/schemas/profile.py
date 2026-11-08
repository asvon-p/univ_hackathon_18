import json
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from shared.contracts import Color, StyleTag

BODY_FIELDS = {"gender", "height_cm", "weight_kg", "birth_year", "age", "avatar_config"}


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nickname: str | None = Field(default=None, min_length=1, max_length=50)
    gender: Literal["male", "female", "other"] | None = None
    height_cm: float | None = Field(default=None, ge=50, le=250, strict=True, allow_inf_nan=False)
    weight_kg: float | None = Field(default=None, ge=20, le=400, strict=True, allow_inf_nan=False)
    age: int | None = Field(default=None, ge=0, le=130, strict=True)
    birth_year: int | None = Field(default=None, ge=1900, strict=True)
    preferred_styles: list[StyleTag] | None = Field(default=None, max_length=9)
    disliked_colors: list[Color] | None = Field(default=None, max_length=22)
    consent_body_info: bool | None = Field(default=None, strict=True)
    avatar_config: dict[str, JsonValue] | None = None

    @field_validator("nickname", mode="before")
    @classmethod
    def strip_nickname(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("nickname", "preferred_styles", "disliked_colors", "consent_body_info")
    @classmethod
    def non_null_when_supplied(cls, value):
        if value is None:
            raise ValueError("이 필드는 null로 변경할 수 없습니다")
        return list(dict.fromkeys(value)) if isinstance(value, list) else value

    @field_validator("birth_year")
    @classmethod
    def valid_year(cls, value):
        if value is not None and value > datetime.now(UTC).year:
            raise ValueError("출생연도는 현재 연도 이하여야 합니다")
        return value

    @field_validator("avatar_config")
    @classmethod
    def bounded_avatar(cls, value):
        if value is not None and len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode()) > 16384:
            raise ValueError("아바타 설정은 16KB 이하여야 합니다")
        return value


class ProfileView(BaseModel):
    user_id: UUID
    nickname: str
    gender: str | None = None
    height_cm: float | None = None
    weight_kg: float | None = None
    age: int | None = None
    body_photo_updated_at: datetime | None = None
    birth_year: int | None = None
    preferred_styles: list[StyleTag]
    disliked_colors: list[Color]
    consent_body_info_at: datetime | None = None
    avatar_config: dict[str, JsonValue] | None = None

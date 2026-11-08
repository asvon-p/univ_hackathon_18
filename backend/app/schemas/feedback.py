from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator


class FeedbackRequest(BaseModel):
    """계약 v1.0의 필드를 유지하되 HTTP 입력은 UUID/실제 bool로 제한합니다."""

    model_config = ConfigDict(extra="forbid")

    recommendation_id: UUID
    outfit_id: UUID
    accepted: StrictBool
    reason: str | None = Field(default=None, max_length=1000)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str | None) -> str | None:
        if value is not None:
            if "\x00" in value:
                raise ValueError("사유에 null 문자를 사용할 수 없습니다")
            return value.strip() or None
        return None


class FeedbackResponse(BaseModel):
    feedback_id: UUID
    recommendation_id: UUID
    outfit_id: UUID | None  # 옷 삭제로 코디가 사라져도 피드백 로그는 남습니다.
    accepted: bool
    reason: str | None
    created_at: datetime

"""API 전용 요청/응답 모델. 계약 모델(shared/contracts.py)을 감싸거나 확장합니다."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.schemas.garment import GarmentView
from backend.app.schemas.weather import Temperature, WeatherResponse
from shared.contracts import Occasion, Outfit, StylePreference, UserContext
from shared.day_plans import OuterwearPolicy


class RecommendationRequest(BaseModel):
    """D의 UserContext 직렬화 지원. 실제 사용자·피드백은 항상 서버에서 구성합니다."""

    model_config = ConfigDict(extra="forbid")

    temperature_c: Temperature | None = Field(default=None, examples=[8.5])
    lat: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False, strict=True)
    lon: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False, strict=True)
    occasion: Occasion | None = None
    preferred_styles: list[StylePreference] = Field(default_factory=list, max_length=20)
    excluded_items: list[UUID] = Field(default_factory=list)
    required_garment_ids: list[UUID] = Field(default_factory=list, max_length=3)
    schedule_id: UUID | None = None
    schedule_version: int | None = Field(default=None, ge=1, strict=True)
    # Shared UserContext may serialize its empty internal snapshot. Never accept supplied content.
    day_plan: None = None
    reference_id: UUID | None = None
    reference_version: int | None = Field(default=None, ge=1, strict=True)
    style_reference: None = None
    outerwear_policy: OuterwearPolicy = "always_worn"
    user_id: Literal["me"] | None = None
    recent_feedback: list = Field(default_factory=list, max_length=0)

    @model_validator(mode="after")
    def require_temperature_or_location(self):
        if (self.reference_id is None) != (self.reference_version is None):
            raise ValueError("참고 사진 ID와 버전을 함께 입력하세요")
        if (self.schedule_id is None) != (self.schedule_version is None):
            raise ValueError("일정 ID와 버전을 함께 입력하세요")
        if len(self.required_garment_ids) != len(set(self.required_garment_ids)):
            raise ValueError("필수 의류를 중복 선택할 수 없습니다")
        if set(self.required_garment_ids) & set(self.excluded_items):
            raise ValueError("같은 옷을 필수 의류와 제외 의류로 선택할 수 없습니다")
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat와 lon을 함께 입력하세요")
        if self.lat is None and self.temperature_c is None:
            raise ValueError("현재 위치(lat/lon) 또는 수동 기온(temperature_c)이 필요합니다")
        return self


class RecommendationResponse(BaseModel):
    recommendation_id: str
    outfits: list[Outfit]
    garments: dict[str, GarmentView] = Field(default_factory=dict, description="화면·2D 아바타용 옷 정보")
    provider: str
    is_fallback: bool
    context: UserContext
    weather: WeatherResponse | None = None
    latency_ms: int
    created_at: datetime

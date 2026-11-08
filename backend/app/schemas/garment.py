"""옷장 API 스키마. 분석 실패한 옷은 category가 null일 수 있습니다."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shared.contracts import Category, Color, GarmentStatus, Pattern, StyleTag


class GarmentView(BaseModel):
    garment_id: str
    name: str | None = None
    location: str | None = None
    category: Category | None = None
    subcategory: str | None = None
    color: Color = "unknown"
    pattern: Pattern = "unknown"
    style_candidates: list[StyleTag] = Field(default_factory=list)
    thermal_level: int | None = None
    status: GarmentStatus = "available"
    cutout_path: str | None = None
    image_url: str | None = None
    cutout_url: str | None = None
    analysis_status: Literal["pending", "done", "failed"] = "done"
    source: Literal["model", "user"] | None = None
    model_version: str | None = None
    error: str | None = None
    analyzed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GarmentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=100)

    category: Category | None = None
    subcategory: str | None = Field(default=None, max_length=50)
    color: Color | None = None
    pattern: Pattern | None = None
    style_candidates: list[StyleTag] | None = None
    thermal_level: int | None = Field(default=None, ge=1, le=5)
    status: GarmentStatus | None = None

    @field_validator("category", "color", "pattern", "style_candidates", "status")
    @classmethod
    def _non_null_when_supplied(cls, value):
        if value is None:
            raise ValueError("이 필드는 null로 변경할 수 없습니다")
        return value


ANALYSIS_FIELDS = frozenset(GarmentPatch.model_fields) - {"status", "name", "location"}

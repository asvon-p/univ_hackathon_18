"""옷장 API 스키마. 분석 실패한 옷은 category가 null일 수 있습니다."""

from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, computed_field, field_validator

from shared.contracts import Category, Color, GarmentStatus, Pattern, StyleTag


class GarmentView(BaseModel):
    garment_id: str
    is_example: bool = False
    name: str | None = None
    location: str | None = None
    category: Category | None = None
    subcategory: str | None = None
    color: Color = "unknown"
    secondary_colors: list[Color] = Field(default_factory=list, max_length=3)
    pattern: Pattern = "unknown"
    style_candidates: list[StyleTag] = Field(default_factory=list)
    thermal_level: int | None = None
    status: GarmentStatus = "available"
    wears_before_laundry: int = 1
    wear_count: int = 0
    cutout_path: str | None = None
    image_url: str | None = None
    cutout_url: str | None = None
    analysis_status: Literal["pending", "done", "failed"] = "done"
    source: Literal["model", "user"] | None = None
    role_source: Literal["model", "user"] | None = None
    model_version: str | None = None
    error: str | None = None
    analyzed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @computed_field
    @property
    def wearing_role(self) -> Category | None:
        return self.category

    @computed_field
    @property
    def garment_type(self) -> str | None:
        return self.subcategory


class GarmentPage(BaseModel):
    items: list[GarmentView]
    next_cursor: str | None
    has_more: bool
    total: int
    all_count: int
    matched_count: int
    counts: dict[str, int]


class GarmentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=100)

    category: Category | None = Field(default=None, validation_alias=AliasChoices("wearing_role", "category"))
    subcategory: str | None = Field(
        default=None, max_length=50, validation_alias=AliasChoices("garment_type", "subcategory")
    )
    color: Color | None = None
    secondary_colors: list[Color] | None = Field(default=None, max_length=3)
    pattern: Pattern | None = None
    style_candidates: list[StyleTag] | None = None
    thermal_level: int | None = Field(default=None, ge=1, le=5)
    status: GarmentStatus | None = None
    wears_before_laundry: int | None = Field(default=None, strict=True, ge=1, le=2)

    @field_validator(
        "category",
        "color",
        "secondary_colors",
        "pattern",
        "style_candidates",
        "status",
        "wears_before_laundry",
    )
    @classmethod
    def _non_null_when_supplied(cls, value):
        if value is None:
            raise ValueError("이 필드는 null로 변경할 수 없습니다")
        return value

    @field_validator("secondary_colors")
    @classmethod
    def validate_palette(cls, value):
        if len(set(value)) != len(value) or set(value) & {"multi", "unknown"}:
            raise ValueError("서브 색상은 중복 없이 실제 색상으로 최대 3개 선택하세요")
        return value


ANALYSIS_FIELDS = frozenset(GarmentPatch.model_fields) - {
    "status",
    "name",
    "location",
    "wears_before_laundry",
}

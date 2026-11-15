from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.contracts import Color, Pattern, StyleTag


class ReferenceFeatureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["top", "bottom", "outerwear"]
    color: Color = "unknown"
    pattern: Pattern = "unknown"
    subcategory: str | None = Field(default=None, max_length=50)
    style_candidates: list[StyleTag] = Field(default_factory=list, max_length=9)


class Region(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: Literal["top", "bottom", "outerwear"]
    x: float = Field(ge=0, lt=1, allow_inf_nan=False)
    y: float = Field(ge=0, lt=1, allow_inf_nan=False)
    width: float = Field(gt=0, le=1, allow_inf_nan=False)
    height: float = Field(gt=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def bounds(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError("의류 영역이 사진 범위를 벗어났습니다")
        return self


class RegionAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1, strict=True)
    region: Region | None = None  # Existing single-region clients remain supported, once per upload.
    regions: list[Region] | None = Field(default=None, min_length=1, max_length=3)

    @model_validator(mode="after")
    def one_batch(self):
        if (self.region is None) == (self.regions is None):
            raise ValueError("분석할 영역을 한 번에 선택해 주세요")
        if len({r.category for r in self.selected}) != len(self.selected):
            raise ValueError("의류 종류별 영역은 하나만 선택하세요")
        return self

    @property
    def selected(self):
        return self.regions if self.regions is not None else [self.region]


class ReferenceConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1, strict=True)
    features: list[ReferenceFeatureInput] = Field(min_length=1, max_length=3)
    focus: Literal["balanced", "color", "style"] = "balanced"

    @model_validator(mode="after")
    def usable_features(self):
        if len({f.category for f in self.features}) != len(self.features):
            raise ValueError("카테고리별 특징은 하나만 확인하세요")
        for feature in self.features:
            if (
                feature.color == "unknown"
                and feature.pattern == "unknown"
                and not feature.subcategory
                and not feature.style_candidates
            ):
                raise ValueError("각 의류에 비교할 색상·패턴·종류·스타일 중 하나 이상 필요합니다")
        return self

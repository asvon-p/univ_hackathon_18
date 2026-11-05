"""팀 공통 데이터 계약 (A·B·C·D 모두 이 파일을 기준으로 개발합니다).

규칙
- 필드를 추가/변경/삭제할 때는 반드시 PR 로 공유하고 전원 확인 후 병합합니다.
- 불확실한 값은 추측하지 말고 None 또는 "unknown" 으로 둡니다.
- JSON 키는 snake_case, ID 는 문자열(UUID 또는 "G001" 형식)로 통일합니다.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

# ---------- 공통 Enum ----------
Category = Literal["top", "bottom", "outerwear", "shoes"]
Pattern = Literal["solid", "stripe", "check", "dot", "floral", "graphic", "other", "unknown"]
GarmentStatus = Literal["available", "laundry", "inactive"]


# ---------- B (Vision) → A, C, D ----------
class GarmentAttributes(BaseModel):
    garment_id: str
    category: Category
    subcategory: Optional[str] = None          # 예: "sweater", "jeans"
    color: Optional[str] = None                # 대표 색상 이름 (예: "navy")
    pattern: Pattern = "unknown"
    style_candidates: list[str] = Field(default_factory=list)  # 예: ["minimal", "casual"]
    thermal_level: Optional[int] = None        # 1(얇음)~5(두꺼움), 모르면 None
    cutout_path: Optional[str] = None          # 투명 PNG 경로 (storage 기준 상대경로)
    status: GarmentStatus = "available"        # C 가 관리 (세탁 중 등)


class VisionResult(BaseModel):
    """B 의 extract() 반환값. 실패해도 예외 대신 success=False 로 반환합니다."""
    success: bool
    attributes: Optional[GarmentAttributes] = None
    model_version: Optional[str] = None
    error: Optional[str] = None


# ---------- C, D → A ----------
class UserContext(BaseModel):
    user_id: str
    temperature_c: float
    occasion: Optional[str] = None             # 예: "daily", "date", "interview"
    preferred_styles: list[str] = Field(default_factory=list)
    excluded_items: list[str] = Field(default_factory=list)  # 제외할 garment_id


# ---------- A → C, D ----------
class OutfitItems(BaseModel):
    top: str
    bottom: str
    shoes: str
    outerwear: Optional[str] = None            # 아우터는 선택


class Outfit(BaseModel):
    outfit_id: str
    items: OutfitItems
    style_tags: list[str] = Field(default_factory=list)
    reason: str = ""
    score: Optional[float] = None


class RecommendationResult(BaseModel):
    recommendation_id: str
    outfits: list[Outfit] = Field(default_factory=list, max_length=3)


# ---------- D, C → A ----------
class FeedbackEvent(BaseModel):
    recommendation_id: str
    outfit_id: str
    accepted: bool
    reason: Optional[str] = None

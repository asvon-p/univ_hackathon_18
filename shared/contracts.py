"""팀 공통 데이터 계약 (A·B·C·D 모두 이 파일을 기준으로 개발합니다).

규칙
- 필드를 추가/변경/삭제할 때는 반드시 PR 로 공유하고 전원 확인 후 병합합니다.
- 불확실한 값은 추측하지 말고 None 또는 "unknown" 으로 둡니다.
- JSON 키는 snake_case, ID 는 UUID 문자열로 통일합니다. (Mock 데이터만 "G001" 형식 허용)
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from shared.day_plans import DayPlanSnapshot, OuterwearPolicy, WearingStep

# 필드 추가(기본값 있음) → 1.x 증가 / 이름 변경·삭제·타입 변경 → 2.0 (전원 합의)
CONTRACT_VERSION = "2.4"

# ---------- 공통 단어 목록 (A·B·D 모두 이 값만 사용) ----------
# 값 추가는 가능하지만, 기존 값의 이름 변경·삭제는 전원 합의 후 PR 로만 합니다.
# 목록이 필요하면 typing.get_args(Color) 처럼 꺼내 쓰세요 (B 의 분류 라벨, D 의 선택지 등).
# Wearing role in an outfit, NOT a taxonomy inferred from garment shape.
Category = Literal["top", "bottom", "outerwear", "shoes"]

Color = Literal[
    "black",
    "white",
    "gray",
    "ivory",
    "beige",
    "brown",
    "camel",
    "khaki",
    "navy",
    "blue",
    "sky_blue",
    "denim",
    "green",
    "olive",
    "red",
    "burgundy",
    "pink",
    "purple",
    "yellow",
    "orange",
    "multi",
    "unknown",
]

Pattern = Literal["solid", "stripe", "check", "dot", "floral", "graphic", "other", "unknown"]

StyleTag = Literal[
    "casual",
    "minimal",
    "street",
    "formal",
    "dandy",
    "sporty",
    "vintage",
    "feminine",
    "amekaji",
]

Occasion = Literal["daily", "work", "date", "interview", "exercise", "travel"]

GarmentStatus = Literal["available", "laundry", "inactive"]
StylePreference = Annotated[str, Field(min_length=1, max_length=100)]
RECOMMENDATION_CATEGORIES = ("top", "bottom", "outerwear")

# 형태는 역할과 독립적인 자유 문자열입니다. 아래 목록은 추천 예시이며 제약이 아닙니다.
SUBCATEGORIES: dict[str, list[str]] = {
    "top": ["t-shirt", "shirt", "blouse", "sweater", "hoodie", "sweatshirt", "knit_vest"],
    "bottom": ["jeans", "slacks", "cotton_pants", "shorts", "skirt", "jogger"],
    "outerwear": ["coat", "padding", "jacket", "cardigan", "blazer", "windbreaker", "shirt"],
    "shoes": ["sneakers", "loafers", "boots", "sandals", "running_shoes", "heels"],
}


# ---------- B (Vision) → A, C, D ----------
class GarmentAttributes(BaseModel):
    garment_id: str
    category: Category | None  # 호환 필드명: 착용 역할. 사용자 선택이 모델 제안보다 우선
    subcategory: str | None = None  # 호환 필드명: 옷 형태. 예: 역할 outerwear + 형태 shirt
    color: Color = "unknown"  # 대표 색상
    pattern: Pattern = "unknown"
    style_candidates: list[StyleTag] = Field(default_factory=list)  # 복수 가능
    thermal_level: int | None = None  # 1(얇음)~5(두꺼움), 모르면 None
    cutout_path: str | None = None  # 투명 PNG 경로 (storage 기준 상대경로)
    status: GarmentStatus = "available"  # C 가 관리 (세탁 중 등)

    @property
    def wearing_role(self) -> Category | None:
        return self.category

    @property
    def garment_type(self) -> str | None:
        return self.subcategory


class ReferenceFeature(BaseModel):
    category: Literal["top", "bottom", "outerwear"]
    color: Color = "unknown"
    pattern: Pattern = "unknown"
    subcategory: str | None = Field(default=None, max_length=50)
    style_candidates: list[StyleTag] = Field(default_factory=list, max_length=9)
    extraction: Literal["vision", "baseline", "manual"] = "manual"


class StyleReferenceSnapshot(BaseModel):
    id: str
    version: int = Field(ge=1)
    features: list[ReferenceFeature] = Field(min_length=1, max_length=3)
    focus: Literal["balanced", "color", "style"] = "balanced"


class ReferenceMatch(BaseModel):
    category: Literal["top", "bottom", "outerwear"]
    matched: list[str] = Field(default_factory=list)
    different: list[str] = Field(default_factory=list)
    fixed: bool = False


class VisionResult(BaseModel):
    """B 의 extract() 반환값. 실패해도 예외 대신 success=False 로 반환합니다."""

    success: bool
    attributes: GarmentAttributes | None = None
    model_version: str | None = None
    error: str | None = None


# ---------- D, C → A ----------
class FeedbackEvent(BaseModel):
    recommendation_id: str
    outfit_id: str
    accepted: bool
    reason: str | None = None


# ---------- C, D → A ----------
class UserContext(BaseModel):
    user_id: str
    temperature_c: float
    occasion: Occasion | None = None
    preferred_styles: list[StylePreference] = Field(default_factory=list, max_length=20)
    excluded_items: list[str] = Field(default_factory=list)  # 제외할 garment_id
    required_garment_ids: list[str] = Field(default_factory=list, max_length=3)
    schedule_id: str | None = None
    schedule_version: int | None = None
    day_plan: DayPlanSnapshot | None = None
    reference_id: str | None = None
    reference_version: int | None = None
    style_reference: StyleReferenceSnapshot | None = None
    outerwear_policy: OuterwearPolicy = "always_worn"
    recent_feedback: list[FeedbackEvent] = Field(default_factory=list, max_length=20)


# ---------- A → C, D ----------
class OutfitItems(BaseModel):
    top: str
    bottom: str
    shoes: str | None = None  # 과거 추천 조회용. 새 추천에서는 항상 None
    outerwear: str | None = None  # 아우터는 선택


class Outfit(BaseModel):
    outfit_id: str
    items: OutfitItems
    style_tags: list[StyleTag] = Field(default_factory=list)
    reason: str = ""
    score: float | None = None
    wearing_plan: list[WearingStep] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    score_components: dict[str, float] = Field(default_factory=dict)
    policy_version: str | None = None
    reference_matches: list[ReferenceMatch] = Field(default_factory=list)


class RecommendationResult(BaseModel):
    recommendation_id: str
    outfits: list[Outfit] = Field(default_factory=list, max_length=3)

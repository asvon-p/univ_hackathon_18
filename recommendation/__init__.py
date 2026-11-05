"""팀원 A — Recommendation Engine.

C(Backend)는 아래 recommend() 함수만 호출합니다. 함수 시그니처는 바꾸지 말고,
내부 구현(규칙 필터, 후보 생성, LLM 평가)을 자유롭게 교체하세요.
"""
from shared.contracts import (
    GarmentAttributes,
    Outfit,
    OutfitItems,
    RecommendationResult,
    UserContext,
)


def recommend(context: UserContext, garments: list[GarmentAttributes]) -> RecommendationResult:
    """임시 구현: 카테고리별 첫 번째 사용 가능한 옷으로 1벌 조합. A 가 교체합니다."""
    usable = [g for g in garments if g.status == "available" and g.garment_id not in context.excluded_items]
    pick = {c: next((g.garment_id for g in usable if g.category == c), None)
            for c in ("top", "bottom", "shoes", "outerwear")}
    outfits = []
    if pick["top"] and pick["bottom"] and pick["shoes"]:
        outfits.append(Outfit(
            outfit_id="O001",
            items=OutfitItems(top=pick["top"], bottom=pick["bottom"], shoes=pick["shoes"],
                              outerwear=pick["outerwear"] if context.temperature_c < 15 else None),
            reason="(stub) 사용 가능한 옷으로 만든 기본 조합",
        ))
    return RecommendationResult(recommendation_id="R-stub", outfits=outfits)

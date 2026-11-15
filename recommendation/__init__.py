"""A의 rules/ranking 결과를 공통 계약으로 변환합니다 (원본 브랜치: 0ba94be)."""

from recommendation.ranking import recommend_top3, supported_preferences
from shared.contracts import (
    RECOMMENDATION_CATEGORIES,
    GarmentAttributes,
    Outfit,
    OutfitItems,
    RecommendationResult,
    UserContext,
)


def recommend(context: UserContext, garments: list[GarmentAttributes]) -> RecommendationResult:
    if context.style_reference is not None:
        from recommendation.style_reference import recommend_reference

        return recommend_reference(context, garments)
    if context.day_plan is not None:
        from recommendation.day_schedule import recommend_day

        return recommend_day(context, garments)
    usable = [
        g
        for g in garments
        if g.status == "available"
        and g.garment_id not in context.excluded_items
        and g.category in RECOMMENDATION_CATEGORIES
    ]
    by_id = {g.garment_id: g for g in usable}
    criteria = "색상·상황·선호 스타일" if supported_preferences(context.preferred_styles) else "색상·상황"
    outfits = []
    for rank, row in enumerate(recommend_top3(usable, context), start=1):
        items = OutfitItems(top=row["top"], bottom=row["bottom"], outerwear=row.get("outerwear"))
        tags = list(
            dict.fromkeys(
                tag
                for gid in (items.top, items.bottom, items.outerwear)
                if gid
                for tag in by_id[gid].style_candidates
            )
        )
        outfits.append(
            Outfit(
                outfit_id=f"rule-{rank}",
                items=items,
                style_tags=tags,
                score=float(row["score"]),
                reason=f"{context.temperature_c:g}°C 날씨 필터와 {criteria} 점수 기준 조합",
            )
        )
    return RecommendationResult(recommendation_id="rules", outfits=outfits)

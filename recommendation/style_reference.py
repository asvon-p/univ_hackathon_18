"""Confirmed garment-region attributes, not facial/body resemblance or a probability score."""

from heapq import nlargest

from recommendation.day_schedule import evaluate as evaluate_day
from recommendation.ranking import (
    calculate_color_score,
    calculate_occasion_score,
    calculate_style_score,
    supported_preferences,
)
from recommendation.rules import is_weather_suitable, iter_outfits
from shared.contracts import Outfit, OutfitItems, RecommendationResult, ReferenceMatch

COLOR_GROUPS = (
    {"black", "gray"},
    {"white", "ivory"},
    {"beige", "camel", "brown", "khaki"},
    {"blue", "navy", "denim", "sky_blue"},
    {"olive", "green"},
    {"red", "burgundy", "pink"},
    {"yellow", "orange"},
)


def compare(feature, garment, focus, required):
    weights = {
        "balanced": (0.4, 0.2, 0.15, 0.25),
        "color": (0.7, 0.1, 0.05, 0.15),
        "style": (0.15, 0.15, 0.2, 0.5),
    }[focus]
    matched, different, terms = [], [], []
    if garment is None:
        return 0, ReferenceMatch(
            category=feature.category,
            different=["이 카테고리를 포함하지 못했습니다. 사용 가능한 옷과 일정 조건을 확인해 주세요"],
        )
    if feature.color != "unknown":
        equal = feature.color == garment.color
        close = any(feature.color in group and garment.color in group for group in COLOR_GROUPS)
        terms.append((weights[0], 1 if equal else (0.5 if close else 0)))
        (matched if equal else different).append(
            f"색상 {feature.color}"
            if equal
            else f"색상 {feature.color} → {garment.color}" + (" (같은 계열)" if close else "")
        )
    if feature.pattern != "unknown":
        equal = feature.pattern == garment.pattern
        terms.append((weights[1], int(equal)))
        (matched if equal else different).append(
            f"패턴 {feature.pattern}" if equal else f"패턴 {feature.pattern} → {garment.pattern}"
        )
    if feature.subcategory:
        equal = feature.subcategory == garment.subcategory
        terms.append((weights[2], int(equal)))
        (matched if equal else different).append(
            f"종류 {feature.subcategory}"
            if equal
            else f"종류 {feature.subcategory} → {garment.subcategory or '미확정'}"
        )
    if feature.style_candidates:
        shared = set(feature.style_candidates) & set(garment.style_candidates)
        terms.append((weights[3], len(shared) / len(set(feature.style_candidates))))
        if shared:
            matched.append("공통 스타일 " + ", ".join(sorted(shared)))
        missing = set(feature.style_candidates) - shared
        if missing:
            different.append("다른/미확정 스타일 " + ", ".join(sorted(missing)))
    fixed = garment.garment_id in required
    if fixed and different:
        different.append("필수 선택 의류를 유지해 참고 사진과 차이가 있습니다")
    score = sum(w * s for w, s in terms) / sum(w for w, _ in terms) * 100 if terms else 0
    return score, ReferenceMatch(category=feature.category, matched=matched, different=different, fixed=fixed)


def evaluate(context, top, bottom, outer):
    day = evaluate_day(context, top, bottom, outer) if context.day_plan else None
    if context.day_plan and day is None:
        return None
    by_cat = {"top": top, "bottom": bottom, "outerwear": outer}
    reference = context.style_reference
    comparisons = [
        compare(f, by_cat[f.category], reference.focus, set(context.required_garment_ids))
        for f in reference.features
    ]
    reference_score = sum(score for score, _ in comparisons) / len(comparisons)
    color = calculate_color_score(top.color, bottom.color, outer.color if outer else None) / 40 * 100
    preference = (
        calculate_style_score(
            top.style_candidates,
            bottom.style_candidates,
            context.preferred_styles,
            outer.style_candidates if outer else None,
        )
        / 60
        * 100
    )
    occasion = (
        calculate_occasion_score(top.style_candidates, bottom.style_candidates, context.occasion) / 60 * 100
    )
    has_preference = bool(supported_preferences(context.preferred_styles))
    terms = [(15, color), (15, preference)] if has_preference else [(15, color)]
    terms += (
        [(35, day["score_components"]["schedule"]), (35, reference_score)]
        if day
        else [(20, occasion), (50, reference_score)]
    )
    total = sum(weight * score for weight, score in terms) / sum(weight for weight, _ in terms)
    limitations = list(day["limitations"]) if day else []
    limitations.append(
        "확인한 의류 영역의 색상·패턴·종류·스타일을 비교한 결과입니다. "
        "핏·소재·치수·얼굴·체형의 유사도는 판단하지 않습니다"
    )
    if len(reference.features) < 3:
        limitations.append("확인한 카테고리만 부분 재현합니다. 신발·가방·액세서리는 추천 범위 밖입니다")
    if any(f.extraction != "vision" for f in reference.features):
        limitations.append(
            "수동 확인 또는 픽셀 색상 추정 특징이 포함되어 있습니다. "
            "전체를 AI 사진 분석 성공으로 보지 않습니다"
        )
    if not context.day_plan and any(
        g and not is_weather_suitable(g.thermal_level, context.temperature_c) for g in (top, bottom, outer)
    ):
        limitations.append(
            "기온 필터를 완화한 대체 조합입니다. 선택 의류는 유지했으며 실제 기온 적합성을 확인해 주세요"
        )
    components = {
        **(day["score_components"] if day else {}),
        "reference": round(reference_score, 2),
        "color": round(color, 2),
        "occasion": round(occasion, 2),
        "preference": round(preference, 2),
        "total": round(total, 2),
    }
    return {
        "wearing_plan": day["wearing_plan"] if day else [],
        "limitations": limitations,
        "reference_matches": [match for _, match in comparisons],
        "score_components": components,
        "policy_version": "reference-attrs-v1",
    }


def recommend_reference(context, garments, *, relax_weather=False):
    def candidates():
        for index, (top, bottom, outer) in enumerate(iter_outfits(garments, context)):
            if (
                not context.day_plan
                and not relax_weather
                and any(
                    g and not is_weather_suitable(g.thermal_level, context.temperature_c)
                    for g in (top, bottom, outer)
                )
            ):
                continue
            details = evaluate(context, top, bottom, outer)
            if details is not None:
                yield details["score_components"]["total"], -index, top, bottom, outer, details

    outfits = []
    for rank, (score, _, top, bottom, outer, details) in enumerate(
        nlargest(3, candidates(), key=lambda c: c[:2]), 1
    ):
        outfits.append(
            Outfit(
                outfit_id=f"reference-{rank}",
                items=OutfitItems(
                    top=top.garment_id,
                    bottom=bottom.garment_id,
                    outerwear=outer.garment_id if outer else None,
                ),
                style_tags=list(dict.fromkeys(top.style_candidates + bottom.style_candidates)),
                reason="필수 의류와 일정 조건을 지키며 확인한 참고 사진의 특징을 내 옷으로 비교한 조합",
                score=score,
                **details,
            )
        )
    return RecommendationResult(recommendation_id="style-reference", outfits=outfits)

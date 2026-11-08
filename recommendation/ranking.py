from recommendation.rules import filter_outfits_by_weather, generate_outfits
from shared.contracts import GarmentAttributes, UserContext


def calculate_style_score(
    top_styles: list[str], bottom_styles: list[str], preferred_styles: list[str]
) -> int:

    # 사용자가 선호 스타일을 선택하지 않은 경우
    if not preferred_styles:
        return 30

    # 상의와 하의가 각각 선호 스타일과 일치하는지 확인
    top_match = any(style in preferred_styles for style in top_styles)

    bottom_match = any(style in preferred_styles for style in bottom_styles)

    # 상의와 하의 모두 일치
    if top_match and bottom_match:
        return 60

    # 둘 중 하나만 일치
    elif top_match or bottom_match:
        return 30

    # 둘 다 일치하지 않음
    else:
        return 0


def calculate_color_score(
    top_color: str | None, bottom_color: str | None, outer_color: str | None = None
) -> int:
    # 색상 정보가 없는 경우 기본 점수 부여
    if not top_color or not bottom_color:
        return 20

    # 색상 이름을 소문자로 통일
    colors = [top_color.lower(), bottom_color.lower()]

    # 아우터가 있으면 평가에 포함
    if outer_color:
        colors.append(outer_color.lower())

    # 색상을 알 수 없는 경우
    if "unknown" in colors or "multi" in colors:
        return 20

    # 무채색 및 기본색
    neutral_colors = {"black", "white", "gray", "ivory"}

    # 모든 의류가 무채색 또는 기본색인 경우
    if all(color in neutral_colors for color in colors):
        return 40

    # 비슷한 색상 계열끼리 묶기
    color_groups = [
        {"beige", "brown", "camel", "khaki", "ivory"},
        {"navy", "blue", "sky_blue", "denim"},
        {"green", "olive"},
        {"red", "burgundy", "pink"},
        {"yellow", "orange"},
    ]

    # 모든 의류가 같은 색상 계열에 속하면 높은 점수
    for group in color_groups:
        if all(color in group for color in colors):
            return 40
    # 차분한 기본색과 포인트 색상 구분
    base_colors = {
        "black",
        "white",
        "gray",
        "ivory",
        "beige",
        "brown",
        "camel",
        "khaki",
        "navy",
        "denim",
        "olive",
    }

    accent_colors = {"red", "burgundy", "pink", "purple", "yellow", "orange", "blue", "sky_blue", "green"}

    # 포인트 색상이 정확히 하나인 경우
    accent_count = sum(color in accent_colors for color in colors)

    if accent_count == 1 and all(color in base_colors or color in accent_colors for color in colors):
        return 40
    # 색상 이름을 기준으로 밝고 어두운 색 분류
    light_colors = {"white", "ivory", "beige", "sky_blue", "pink", "yellow"}

    dark_colors = {"black", "gray", "navy", "brown", "burgundy", "olive"}

    # 밝은 색과 어두운 색이 모두 포함된 경우
    has_light = any(color in light_colors for color in colors)
    has_dark = any(color in dark_colors for color in colors)

    if has_light and has_dark:
        return 40

    # 위 조건에 해당하지 않는 조합의 기본 점수
    return 20


# 상황별로 적합한 패션 스타일
OCCASION_STYLES = {
    "daily": {"casual", "minimal", "street", "vintage", "amekaji"},
    "work": {"formal", "dandy", "minimal"},
    "date": {"minimal", "dandy", "feminine", "casual"},
    "interview": {"formal", "dandy"},
    "exercise": {"sporty", "casual"},
    "travel": {"casual", "sporty", "street", "amekaji"},
}


def calculate_occasion_score(top_styles: list[str], bottom_styles: list[str], occasion: str | None) -> int:

    # 상황을 선택하지 않았거나 알 수 없는 경우
    if not occasion or occasion not in OCCASION_STYLES:
        return 30

    # 선택한 상황에 적합한 스타일 목록
    suitable_styles = OCCASION_STYLES[occasion]

    # 상의와 하의의 스타일 적합 여부
    top_match = any(style in suitable_styles for style in top_styles)

    bottom_match = any(style in suitable_styles for style in bottom_styles)

    # 양쪽 모두 적합
    if top_match and bottom_match:
        return 60

    # 한쪽만 적합
    elif top_match or bottom_match:
        return 30

    # 둘 다 적합하지 않음
    else:
        return 0


def calculate_total_score(
    top_color: str | None,
    bottom_color: str | None,
    top_styles: list[str],
    bottom_styles: list[str],
    occasion: str | None,
    outer_color: str | None = None,
) -> int:

    # 색상 조화도 (40점 만점)
    color_score = calculate_color_score(top_color, bottom_color, outer_color)

    # 상황 적합도 (60점 만점)
    occasion_score = calculate_occasion_score(top_styles, bottom_styles, occasion)

    # 최종 점수 (100점 만점)
    return color_score + occasion_score


def rank_outfits(outfits: list[dict]) -> list[dict]:
    # 점수가 높은 순서대로 정렬
    ranked = sorted(outfits, key=lambda outfit: outfit["score"], reverse=True)

    # 상위 3개 코디만 반환
    return ranked[:3]


def score_garment_pair(
    top: GarmentAttributes,
    bottom: GarmentAttributes,
    context: UserContext,
    outer: GarmentAttributes | None = None,
) -> int:

    return calculate_total_score(
        top_color=top.color,
        bottom_color=bottom.color,
        top_styles=top.style_candidates,
        bottom_styles=bottom.style_candidates,
        occasion=context.occasion,
        outer_color=outer.color if outer else None,
    )


def score_outfits(outfits: list[dict], garments: list[GarmentAttributes], context: UserContext) -> list[dict]:

    # 옷 ID로 옷 정보를 찾기 위한 사전
    garment_map = {garment.garment_id: garment for garment in garments}

    scored_outfits = []

    for outfit in outfits:
        top = garment_map[outfit["top"]]
        bottom = garment_map[outfit["bottom"]]

        # 아우터가 있으면 정보 가져오기
        outer_id = outfit.get("outerwear")
        outer = garment_map[outer_id] if outer_id else None

        # 상의, 하의, 아우터 색상 반영
        score = score_garment_pair(top, bottom, context, outer)

        scored_outfits.append({**outfit, "score": score})

    return scored_outfits


def recommend_top3(garments: list[GarmentAttributes], context: UserContext) -> list[dict]:

    # 1. 가능한 코디 조합 생성
    outfits = generate_outfits(garments)

    # 2. 날씨에 맞지 않는 코디 제외
    filtered_outfits = filter_outfits_by_weather(outfits, context.temperature_c)

    # 3. 코디 정보를 딕셔너리로 변환
    outfit_dicts = []

    for top, bottom, outer in filtered_outfits:
        outfit_dicts.append(
            {
                "top": top.garment_id,
                "bottom": bottom.garment_id,
                "outerwear": outer.garment_id if outer else None,
            }
        )

    # 4. 코디 점수 계산
    scored_outfits = score_outfits(outfit_dicts, garments, context)

    # 5. 점수 상위 3개 반환
    return rank_outfits(scored_outfits)

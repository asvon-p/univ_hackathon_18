"""Local 추천의 선호 반영, 기존 점수와의 균형, 아우터/미선택 회귀 검증."""

from itertools import product

import pytest

from recommendation import recommend
from recommendation.ranking import calculate_color_score, calculate_occasion_score, calculate_total_score
from shared.contracts import GarmentAttributes, OutfitItems, UserContext


def wardrobe():
    return [
        GarmentAttributes(
            garment_id=f"{style}-{category}",
            category=category,
            color="white" if category == "top" else "black",
            style_candidates=[style],
            thermal_level=3,
        )
        for style in ("street", "minimal")
        for category in ("top", "bottom")
    ]


def context(styles, **kwargs):
    return UserContext(user_id="local-ranking-test", temperature_c=15, preferred_styles=styles, **kwargs)


def test_switching_preference_reverses_ranking_with_same_colors_and_occasion():
    garments = wardrobe()
    for style in ("minimal", "street"):
        result = recommend(context([style], occasion="daily"), garments)
        assert result.outfits[0].items == OutfitItems(top=f"{style}-top", bottom=f"{style}-bottom")
        assert [outfit.score for outfit in result.outfits] == [100, 85, 85]
        assert all("선호 스타일" in outfit.reason for outfit in result.outfits)
    assert [g.style_candidates for g in garments] == [["street"], ["street"], ["minimal"], ["minimal"]]


@pytest.mark.parametrize("preferences", [[], ["꾸안꾸"], ["not-a-tag"]])
def test_no_supported_preference_preserves_exact_legacy_scores_and_order(preferences):
    for top_color, bottom_color, top_style, bottom_style, occasion in product(
        ("white", "unknown"), ("black", "multi"), ("formal", "casual"), ("minimal", "sporty"), (None, "work")
    ):
        legacy = calculate_color_score(top_color, bottom_color) + calculate_occasion_score(
            [top_style], [bottom_style], occasion
        )
        assert (
            calculate_total_score(
                top_color, bottom_color, [top_style], [bottom_style], occasion, preferred_styles=preferences
            )
            == legacy
        )
    result = recommend(context(preferences, occasion="daily"), wardrobe())
    assert result.outfits[0].items == OutfitItems(top="street-top", bottom="street-bottom")
    assert [o.score for o in result.outfits] == [100, 100, 100]
    assert all("선호 스타일" not in o.reason for o in result.outfits)


def test_occasion_can_outweigh_preference_and_color_still_breaks_ties():
    def score(top, bottom, color="white"):
        return calculate_total_score(
            color, "black", [top], [bottom], "interview", preferred_styles=["casual"]
        )

    # 면접에 둘 다 적합한 코디(70)는 선호만 맞는 코디(58)보다 우선합니다.
    assert score("formal", "formal") == 70
    assert score("casual", "casual") == 58
    assert score("formal", "formal") > score("casual", "casual")
    # 상황과 선호가 같으면 색상 조화 차이(14점)가 유지됩니다.
    assert score("casual", "casual") - score("casual", "casual", "unknown") == 14


def test_outerwear_preference_counts_without_bonus_for_extra_tags_or_items():
    top = GarmentAttributes(garment_id="t", category="top", color="white", style_candidates=["street"])
    bottom = GarmentAttributes(garment_id="b", category="bottom", color="black", style_candidates=["street"])
    outers = [
        GarmentAttributes(
            garment_id=f"o-{style}", category="outerwear", color="gray", style_candidates=[style]
        )
        for style in ("street", "minimal")
    ]
    result = recommend(context(["minimal"], occasion="daily"), [top, bottom, *outers])
    assert result.outfits[0].items.outerwear == "o-minimal"
    assert [o.score for o in result.outfits] == [80, 70, 70]
    args = ("white", "black", ["minimal", "casual"], ["minimal"], "daily")
    normal = calculate_total_score(*args, preferred_styles=["minimal"])
    duplicate = calculate_total_score(*args, preferred_styles=[" MINIMAL ", "minimal", "casual", "꾸안꾸"])
    assert normal == duplicate == 100
    assert calculate_total_score(*args, preferred_styles=["minimal"], outer_styles=["minimal"]) == 100
    assert calculate_total_score(*args, preferred_styles=["minimal"], outer_styles=[]) == 90


def test_preferred_clothes_still_respect_weather_status_exclusion_and_top_three():
    garments = wardrobe()
    garments[2].thermal_level = 5  # 선호 상의라도 15도에 부적합
    garments.append(
        garments[2].model_copy(update={"garment_id": "laundry", "thermal_level": 3, "status": "laundry"})
    )
    garments.append(garments[2].model_copy(update={"garment_id": "excluded", "thermal_level": 3}))
    result = recommend(context(["minimal"], excluded_items=["excluded"]), garments)
    assert result.outfits and len(result.outfits) <= 3
    assert all(o.items.top == "street-top" and 0 <= o.score <= 100 for o in result.outfits)


def test_multiple_preferences_are_equal_alternatives_and_unknown_labels_do_not_dilute_them():
    garments = wardrobe()
    several = recommend(context(["minimal", "street"], occasion="daily"), garments)
    assert [o.score for o in several.outfits] == [100, 100, 100]
    assert recommend(context(["minimal", "꾸안꾸"]), garments) == recommend(context(["minimal"]), garments)

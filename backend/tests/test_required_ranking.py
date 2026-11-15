import pytest

from backend.app.adapters.recommender import LocalRecommender, MockRecommender, validate_result
from shared.contracts import GarmentAttributes, UserContext


@pytest.mark.parametrize("provider", [LocalRecommender, MockRecommender])
@pytest.mark.parametrize("slots", [["top"], ["bottom"], ["outerwear"], ["top", "bottom", "outerwear"]])
def test_low_ranked_required_items_are_fixed_before_top_three(provider, slots):
    garments = [
        GarmentAttributes(
            garment_id=f"{category}-{i}",
            category=category,
            color="navy",
            thermal_level=3,
            style_candidates=["minimal"] if i < 5 else ["street"],
        )
        for category in ("top", "bottom", "outerwear")
        for i in range(6)
    ]
    required = [f"{slot}-5" for slot in slots]
    context = UserContext(
        user_id="owner", temperature_c=18, preferred_styles=["minimal"], required_garment_ids=required
    )
    result = provider().recommend(context, garments)
    assert result.outfits
    assert all(set(required) <= set(o.items.model_dump().values()) for o in result.outfits)
    if len(slots) == 3:
        assert len(result.outfits) == 1


@pytest.mark.parametrize(
    "required,excluded", [(["missing"], []), (["top", "top"], []), (["top"], ["top"]), (["top", "top2"], [])]
)
def test_providers_and_validator_fail_closed_for_invalid_context(required, excluded):
    garments = [
        GarmentAttributes(garment_id=gid, category=cat)
        for gid, cat in (
            ("top", "top"),
            ("top2", "top"),
            ("bottom", "bottom"),
        )
    ]
    normal = UserContext(user_id="u", temperature_c=18)
    valid = MockRecommender().recommend(normal, garments)
    context = normal.model_copy(update={"required_garment_ids": required, "excluded_items": excluded})
    assert not LocalRecommender().recommend(context, garments).outfits
    assert not MockRecommender().recommend(context, garments).outfits
    assert not validate_result(valid, context, garments)[0].outfits

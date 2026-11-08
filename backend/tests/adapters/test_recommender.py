import time

from backend.app.adapters.recommender import MockRecommender, SafeRecommender, validate_result
from shared.contracts import GarmentAttributes, Outfit, OutfitItems, RecommendationResult, UserContext

G = [
    GarmentAttributes(garment_id="T1", category="top", style_candidates=["minimal"]),
    GarmentAttributes(garment_id="T2", category="top", status="laundry"),
    GarmentAttributes(garment_id="B1", category="bottom"),
    GarmentAttributes(garment_id="S1", category="shoes"),
    GarmentAttributes(garment_id="O1", category="outerwear", thermal_level=5),
]
CTX = UserContext(user_id="U1", temperature_c=3)


def outfit(oid, top="T1", bottom="B1", shoes="S1", outer=None):
    return Outfit(outfit_id=oid, items=OutfitItems(top=top, bottom=bottom, shoes=shoes, outerwear=outer))


class Fixed:
    name = "local"

    def __init__(self, outfits=None, exc=None, sleep=0):
        self.outfits, self.exc, self.sleep = outfits or [], exc, sleep

    def recommend(self, context, garments):
        time.sleep(self.sleep)
        if self.exc:
            raise self.exc
        return RecommendationResult(recommendation_id="R", outfits=self.outfits)


def test_mock_adds_outerwear_when_cold():
    r = MockRecommender().recommend(CTX, G)
    assert r.outfits and r.outfits[0].items.outerwear == "O1"
    assert all(o.items.top != "T2" for o in r.outfits)  # 세탁 중 제외


def test_validate_drops_laundry_unknown_wrong_slot_and_duplicates():
    # 계약상 최대 3개라서 두 번에 나눠 검사
    raw1 = RecommendationResult(
        recommendation_id="R", outfits=[outfit("ok"), outfit("dup"), outfit("laundry", top="T2")]
    )
    result, dropped = validate_result(raw1, CTX, G)
    assert [o.outfit_id for o in result.outfits] == ["ok"] and len(dropped) == 2

    raw2 = RecommendationResult(
        recommendation_id="R", outfits=[outfit("unknown", bottom="X9"), outfit("slot", top="S1")]
    )
    result, dropped = validate_result(raw2, CTX, G)
    assert result.outfits == [] and len(dropped) == 2


def test_excluded_items_respected():
    ctx = UserContext(user_id="U1", temperature_c=20, excluded_items=["T1"])
    result, _ = validate_result(RecommendationResult(recommendation_id="R", outfits=[outfit("a")]), ctx, G)
    assert result.outfits == []


def test_success_uses_inner_provider():
    out = SafeRecommender(Fixed([outfit("a")]), timeout_sec=1).recommend(CTX, G)
    assert out.provider == "local" and not out.is_fallback


def test_exception_falls_back():
    out = SafeRecommender(Fixed(exc=RuntimeError("LLM down")), timeout_sec=1).recommend(CTX, G)
    assert out.is_fallback and out.provider == "fallback" and out.result.outfits


def test_timeout_falls_back():
    out = SafeRecommender(Fixed([outfit("a")], sleep=0.5), timeout_sec=0.1).recommend(CTX, G)
    assert out.is_fallback


def test_all_invalid_falls_back():
    out = SafeRecommender(Fixed([outfit("bad", top="T2")]), timeout_sec=1).recommend(CTX, G)
    assert out.is_fallback and out.result.outfits[0].items.top == "T1"


def test_engine_cannot_mutate_validation_or_fallback_inputs():
    class Mutating:
        name = "local"

        def recommend(self, context, garments):
            context.excluded_items.clear()
            garments[0].garment_id = "foreign"
            garments[1].status = "available"
            raise RuntimeError("failed after changing input")

    context = UserContext(user_id="U1", temperature_c=5, excluded_items=["T2"])
    garments = [g.model_copy(deep=True) for g in G]
    outcome = SafeRecommender(Mutating(), 1).recommend(context, garments)
    assert outcome.is_fallback and outcome.result.outfits[0].items.top == "T1"
    assert context.excluded_items == ["T2"]
    assert garments[0].garment_id == "T1" and garments[1].status == "laundry"


def test_mutated_pydantic_outfit_is_revalidated():
    invalid = outfit("invalid")
    invalid.items.top = None
    result, dropped = validate_result(
        RecommendationResult(recommendation_id="R", outfits=[invalid, outfit("valid")]), CTX, G
    )
    assert [o.outfit_id for o in result.outfits] == ["valid"] and len(dropped) == 1


def test_mock_ranks_large_wardrobe_without_expanding_every_combination():
    garments = [
        GarmentAttributes(
            garment_id=f"{category}-{i}",
            category=category,
            style_candidates=["minimal"] if i >= 97 else ["casual"],
        )
        for category in ("top", "bottom", "shoes")
        for i in range(100)
    ]
    result = MockRecommender().recommend(
        UserContext(user_id="U1", temperature_c=20, preferred_styles=["minimal"]), garments
    )
    assert len(result.outfits) == 3
    assert all(o.score == 2 for o in result.outfits)
    assert result.outfits[0].items == OutfitItems(top="top-97", bottom="bottom-97")

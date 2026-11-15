import asyncio

import pytest

from frontend import recommendation_ui as ui
from frontend.connect.auth_profile import Profile
from frontend.connect.garment_storage import Garment
from frontend.connect.recommendation import Outfit, OutfitItems, RecommendationResult


@pytest.mark.parametrize("unavailable", [False, True])
def test_home_reads_saved_results_and_never_generates(monkeypatch, unavailable):
    calls = []
    stored = RecommendationResult(
        recommendation_id="saved",
        outfits=[Outfit(outfit_id="outfit", items=OutfitItems(top="t", bottom="b"))],
    )

    async def read(token):
        calls.append(token)
        return [stored] if token == "owner" else []

    async def unexpected(*args, **kwargs):
        pytest.fail("GET home must not generate or call GPT")

    monkeypatch.setattr(ui.rec, "recent_results", read)
    monkeypatch.setattr(ui.rec, "recommend", unexpected)
    garments = [Garment(garment_id="t", category="top"), Garment(garment_id="b", category="bottom")]
    if unavailable:
        garments.pop()

    async def run():
        for _ in range(3):
            result = await ui.latest_outfit("owner", garments)
            assert result == (None if unavailable else stored.outfits[0])
        assert await ui.latest_outfit("other", garments) is None
        assert calls == ["owner", "owner", "owner", "other"]

    asyncio.run(run())


def test_profile_connector_keeps_preferences_without_overwriting_them_in_body_forms():
    profile = Profile.from_json({"preferred_styles": ["minimal"]})
    assert profile.preferred_styles == ["minimal"]
    assert "preferred_styles" not in profile.to_json()
    assert Profile.from_json({}).preferred_styles == []

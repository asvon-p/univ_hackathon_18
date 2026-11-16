from PIL import Image, ImageDraw

from recommendation.personalization import feedback_adjustment
from recommendation.ranking import calculate_palette_score, score_garment_pair
from shared.contracts import FeedbackEvent, GarmentAttributes, UserContext
from shared.seasonal_weather import seasonal_temperature
from vision.colors import extract_palette


def garment(gid, category, color, **kwargs):
    return GarmentAttributes(garment_id=gid, category=category, color=color, **kwargs)


def test_palette_finds_primary_and_secondary_without_background_or_trim():
    image = Image.new("RGBA", (120, 120), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 110, 70), fill="red")
    draw.rectangle((10, 71, 110, 110), fill="blue")
    primary, secondary = extract_palette(image.convert("RGB"), image)
    assert primary == "red" and "blue" in secondary
    assert "white" not in secondary and "multi" not in [primary, *secondary]


def test_primary_color_dominates_but_secondary_influences_ranking():
    top = garment("t", "top", "white")
    bottom = garment("b", "bottom", "white")
    baseline = calculate_palette_score(top, bottom)
    top.secondary_colors = ["red"]
    bottom.secondary_colors = ["blue"]
    changed = calculate_palette_score(top, bottom)
    assert baseline == 40 and 28 <= changed < baseline


def test_feedback_codes_change_ranking_but_do_not_multiply_repeated_votes():
    old = [
        garment("t", "top", "red", style_candidates=["street"]),
        garment("b", "bottom", "blue", style_candidates=["street"]),
    ]
    new = [
        garment("t2", "top", "white", style_candidates=["minimal"]),
        garment("b2", "bottom", "black", style_candidates=["minimal"]),
    ]
    event = FeedbackEvent(
        recommendation_id="r", outfit_id="o", accepted=False, reason_codes=["colors", "style"], garments=old
    )
    context = UserContext(user_id="u", temperature_c=20, recent_feedback=[event] * 20)
    assert feedback_adjustment(context, *old) == -15
    assert feedback_adjustment(context, *new) == 0
    before = score_garment_pair(*old, UserContext(user_id="u", temperature_c=20))
    assert score_garment_pair(*old, context) == before - 15
    context.recent_feedback.insert(0, event.model_copy(update={"accepted": True}))
    assert feedback_adjustment(context, *old) == 6


def test_season_without_month_uses_current_month_only_in_matching_season(monkeypatch):
    monkeypatch.setattr("shared.seasonal_weather.current_month", lambda: 11)
    assert seasonal_temperature("autumn") == (11, 7.9)
    assert seasonal_temperature("summer") == (7, 24.8)

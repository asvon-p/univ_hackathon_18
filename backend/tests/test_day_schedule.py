from backend.tests.api.test_day_plans import plan_body
from recommendation.day_schedule import evaluate, recommend_day
from shared.contracts import GarmentAttributes, UserContext
from shared.day_plans import DayPlanSnapshot


def setup():
    plan = DayPlanSnapshot(id="test", version=1, **plan_body())
    context = UserContext(user_id="test", temperature_c=35, day_plan=plan, outerwear_policy="removable")

    def garment(gid, category, style, thermal):
        return GarmentAttributes(
            garment_id=gid, category=category, color="black", style_candidates=[style], thermal_level=thermal
        )

    garments = [
        garment("sport", "top", "sporty", 2),
        garment("formal", "top", "formal", 2),
        garment("bottom", "bottom", "formal", 2),
        garment("outer", "outerwear", "formal", 4),
    ]
    return context, garments


def test_purpose_changes_rank_and_current_weather_does_not_override_schedule():
    context, garments = setup()
    context.day_plan.events = context.day_plan.events[:1]
    context.day_plan.events[0].purpose = "interview"
    assert recommend_day(context, garments).outfits[0].items.top == "formal"
    context.day_plan.events[0].purpose = "exercise"
    assert recommend_day(context, garments).outfits[0].items.top == "sport"
    before = recommend_day(context, garments).model_dump()
    context.temperature_c = -20
    assert recommend_day(context, garments).model_dump() == before


def test_policy_range_priority_and_time_weight():
    context, garments = setup()
    _, top, bottom, outer = garments
    result = evaluate(context, top, bottom, outer)
    assert [s.outerwear_action for s in result["wearing_plan"]] == ["carry", "wear"]
    context.outerwear_policy = "always_worn"
    assert all(s.outerwear_action == "wear" for s in evaluate(context, top, bottom, outer)["wearing_plan"])
    context.day_plan.events[0].purpose = "interview"
    context.day_plan.events[1].purpose = "exercise"
    base = evaluate(context, top, bottom, outer)["score_components"]["schedule_mean"]
    context.day_plan.events[0].priority = "high"
    assert evaluate(context, top, bottom, outer)["score_components"]["schedule_mean"] > base
    context.day_plan.events[0].priority = "normal"
    from datetime import timedelta

    context.day_plan.events[0].end_at += timedelta(hours=1)
    assert evaluate(context, top, bottom, outer)["score_components"]["schedule_mean"] > base


def test_constraints_and_missing_data_are_explained():
    context, garments = setup()
    context.required_garment_ids = ["sport", "outer"]
    result = recommend_day(context, garments)
    assert all(o.items.top == "sport" and o.items.outerwear == "outer" for o in result.outfits)
    context.day_plan.events[0].required_style = "formal"
    assert recommend_day(context, garments).outfits == []
    context.day_plan.events[0].required_style = None
    context.day_plan.events[1].environment = "mixed"
    context.day_plan.events[1].outdoor_minutes = None
    result = recommend_day(context, garments).outfits[0]
    assert "임시 50%" in result.wearing_plan[1].note
    assert "보온 적합성은 판단하지 않음" in result.wearing_plan[0].note
    assert result.wearing_plan[1].outerwear_action == "adjust"

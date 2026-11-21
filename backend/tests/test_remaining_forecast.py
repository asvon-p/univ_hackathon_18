from datetime import datetime, timedelta

import pytest

from backend.app.adapters.recommender import LocalRecommender, MockRecommender, validate_result
from backend.tests.test_thermal_policy import garment
from shared.contracts import UserContext
from shared.day_plans import DayPlanSnapshot
from shared.thermal_policy import evaluate_thermal_check, outfit_violation, thermal_checks
from shared.weather_forecast import KST, ForecastPoint, refresh_window, remaining_forecast


def hourly_day(now, *, low=3, high=25):
    start = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    return [
        ForecastPoint(at=start + timedelta(hours=i), temperature_c=high if i < 18 else low) for i in range(25)
    ]


def test_remaining_window_excludes_past_hours_and_next_midnight():
    now = datetime(2026, 11, 21, 14, 30, tzinfo=KST)
    points = hourly_day(now)
    points[5].temperature_c = -20
    points[24].temperature_c = 45
    snapshot = remaining_forecast(points, now, now=now)
    assert snapshot.minimum_c == 3 and snapshot.maximum_c == 25
    assert snapshot.start_at == now and snapshot.end_at == datetime(2026, 11, 22, tzinfo=KST)
    later = refresh_window(snapshot, now=now + timedelta(minutes=20))
    assert later.start_at > snapshot.start_at
    with pytest.raises(ValueError):
        refresh_window(snapshot, now=snapshot.end_at)
    with pytest.raises(ValueError):
        refresh_window(snapshot, now=now + timedelta(minutes=31))


def test_2359_uses_only_current_estimate_and_rejects_missing_last_hour():
    now = datetime(2026, 11, 21, 23, 59, tzinfo=KST)
    points = hourly_day(now)
    snapshot = remaining_forecast(points, now, now=now)
    assert snapshot.minimum_c == snapshot.maximum_c == 3
    with pytest.raises(ValueError):
        remaining_forecast(points[:-1], now, now=now)


def test_missing_forecast_hour_is_rejected_instead_of_interpolating_a_long_gap():
    now = datetime(2026, 11, 21, 14, 30, tzinfo=KST)
    points = hourly_day(now)
    del points[15]
    with pytest.raises(ValueError):
        remaining_forecast(points, now, now=now)


def plan(environment="indoor"):
    return DayPlanSnapshot(
        id="test",
        version=1,
        date="2026-11-21",
        events=[
            {
                "start_at": "2026-11-21T15:00:00+09:00",
                "end_at": "2026-11-21T23:00:00+09:00",
                "purpose": "class",
                "environment": environment,
            }
        ],
    )


@pytest.mark.parametrize("provider", [LocalRecommender, MockRecommender])
def test_winter_indoor_top_is_exactly_one_step_lower_but_bottom_and_outer_stay_warm(provider):
    context = UserContext(user_id="u", temperature_c=-3, clothing_season="winter", day_plan=plan())
    clothes = [garment("t", "top", 3), garment("b", "bottom", 4), garment("o", "outerwear", 5)]
    result = provider().recommend(context, clothes)
    assert result.outfits and result.outfits[0].items.outerwear == "o"
    assert "1단계" in result.outfits[0].wearing_plan[0].note
    assert not validate_result(result, context, clothes)[1]
    for index in (0, 1, 2):
        changed = list(clothes)
        changed[index] = changed[index].model_copy(update={"thermal_level": clothes[index].thermal_level - 1})
        assert outfit_violation(context, *changed)
    context.day_plan = plan("outdoor")
    assert not provider().recommend(context, clothes).outfits
    context.day_plan = plan()
    context.clothing_season = "spring"
    assert not provider().recommend(context, clothes).outfits


@pytest.mark.parametrize("provider", [LocalRecommender, MockRecommender])
def test_summer_indoor_prefers_thin_outer_and_does_not_drop_required_top(provider):
    context = UserContext(
        user_id="u",
        temperature_c=28,
        clothing_season="summer",
        day_plan=plan(),
        required_garment_ids=["t"],
        outerwear_policy="removable",
    )
    clothes = [
        garment("t", "top", 1),
        garment("b", "bottom", 1),
        garment("thin", "outerwear", 1),
        garment("thick", "outerwear", 4),
    ]
    result = provider().recommend(context, clothes)
    assert result.outfits[0].items.top == "t" and result.outfits[0].items.outerwear == "thin"
    assert result.outfits[0].wearing_plan[0].outerwear_action == "wear"
    assert "냉방" in result.outfits[0].wearing_plan[0].note
    assert outfit_violation(
        context, clothes[0].model_copy(update={"thermal_level": 3}), clothes[1], clothes[2]
    )


@pytest.mark.parametrize("provider", [LocalRecommender, MockRecommender])
def test_hot_departure_cold_return_keeps_warm_outer_and_carries_it_in_heat(provider):
    now = datetime(2026, 11, 21, 14, 30, tzinfo=KST)
    context = UserContext(
        user_id="u",
        temperature_c=25,
        clothing_season="autumn",
        weather_forecast=remaining_forecast(hourly_day(now), now, now=now),
        outerwear_policy="removable",
        required_garment_ids=["t"],
    )
    clothes = [garment("t", "top", 3), garment("b", "bottom", 3), garment("o", "outerwear", 4)]
    result = provider().recommend(context, clothes)
    assert result.outfits and all(o.items.outerwear == "o" for o in result.outfits)
    assert not validate_result(result, context, clothes)[1]
    _, action, note = evaluate_thermal_check(context, thermal_checks(context)[0], *clothes)
    assert action == "adjust" and "추운 때" in note

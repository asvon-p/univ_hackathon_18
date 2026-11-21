from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
import pytest

from backend.app.core.config import get_settings
from backend.app.db.models import Recommendation
from backend.app.deps import get_weather
from backend.app.schemas.weather import WeatherResponse
from backend.app.services.weather_forecast import forecast_token
from backend.tests.api.test_day_plans import create_plan, plan_body
from backend.tests.api.test_recommendations import recommend
from backend.tests.test_remaining_forecast import hourly_day
from shared.weather_forecast import remaining_forecast


def sample_forecast():
    now = datetime.now(UTC)
    return remaining_forecast(hourly_day(now, low=3, high=3), now, now=now)


def test_forecast_cannot_be_attached_to_another_dates_schedule(api_client, auth_account, closet):
    snapshot = sample_forecast()
    plan = create_plan(api_client, auth_account)
    result = recommend(
        api_client,
        auth_account,
        forecast_token=forecast_token(snapshot, auth_account["id"]),
        schedule_id=plan["id"],
        schedule_version=1,
    )
    assert result.status_code == 422
    assert "오늘 일정" in result.json()["detail"]


def test_weather_quote_is_owned_forecast_recommendation_and_read_only_replay(
    api_client, auth_account, account_factory, closet, db_session
):
    class Weather:
        def current(self, *args):
            return WeatherResponse(temperature_c=25, source="open_meteo", forecast=sample_forecast())

    api_client.app.dependency_overrides[get_weather] = lambda: Weather()
    reading = api_client.get("/api/v1/weather?lat=37&lon=127", headers=auth_account["headers"])
    assert reading.status_code == 200
    quote = reading.json()["forecast_token"]
    assert quote and reading.json()["forecast"]["minimum_c"] == 3
    key = str(uuid4())
    body = {
        "temperature_c": 25,
        "clothing_season": "autumn",
        "forecast_token": quote,
        "wardrobe": "personal",
        "required_garment_ids": [closet["top"]],
    }
    headers = {**auth_account["headers"], "Idempotency-Key": key}
    response = api_client.post("/api/v1/recommendations", headers=headers, json=body)
    assert response.status_code == 201, response.text
    result = response.json()
    assert all(o["items"]["outerwear"] == closet["outerwear"] for o in result["outfits"])
    assert result["context"]["weather_forecast"]["minimum_c"] == 3
    assert result["context"]["clothing_season"] == "autumn"
    row = db_session.get(Recommendation, UUID(result["recommendation_id"]))
    assert "forecast_token" not in row.context
    assert row.context["weather"]["forecast_token"] is None
    assert not {"lat", "lon"} & set(row.context)
    assert api_client.post("/api/v1/recommendations", headers=headers, json=body).json() == result
    assert (
        api_client.get(
            "/api/v1/recommendations/" + result["recommendation_id"], headers=auth_account["headers"]
        ).json()
        == result
    )
    other = account_factory()
    assert (
        api_client.post(
            "/api/v1/recommendations", headers=other["headers"], json={**body, "required_garment_ids": []}
        ).status_code
        == 422
    )
    assert (
        api_client.get(
            "/api/v1/recommendations/" + result["recommendation_id"], headers=other["headers"]
        ).status_code
        == 404
    )
    # A weather quote can never be used as an access token.
    assert api_client.get("/api/v1/garments", headers={"Authorization": "Bearer " + quote}).status_code == 401


@pytest.mark.parametrize("kind", ["expired", "tampered", "raw-snapshot"])
def test_invalid_forecast_is_not_silently_replaced_by_current_temperature(
    api_client, auth_account, closet, kind
):
    snapshot = sample_forecast()
    quote = forecast_token(snapshot, auth_account["id"])
    body = {"temperature_c": 25, "forecast_token": quote}
    if kind == "expired":
        payload = jwt.decode(quote, get_settings().JWT_SECRET, algorithms=["HS256"])
        payload["exp"] = datetime.now(UTC) - timedelta(seconds=1)
        body["forecast_token"] = jwt.encode(payload, get_settings().JWT_SECRET, algorithm="HS256")
    elif kind == "tampered":
        body["forecast_token"] = "invalid." + quote
    else:
        body["weather_forecast"] = snapshot.model_dump(mode="json")
    assert (
        api_client.post("/api/v1/recommendations", headers=auth_account["headers"], json=body).status_code
        == 422
    )


def test_indoor_winter_and_summer_seasons_are_independent_of_monthly_weather(
    api_client, auth_account, closet, add_garment
):
    body = plan_body()
    body["events"] = body["events"][:1]
    plan = create_plan(api_client, auth_account, body)
    for role, level in [("top", 3), ("bottom", 4), ("outerwear", 5)]:
        assert (
            api_client.patch(
                "/api/v1/garments/" + closet[role],
                headers=auth_account["headers"],
                json={"thermal_level": level},
            ).status_code
            == 200
        )
    result = recommend(
        api_client,
        auth_account,
        temperature_c=-3,
        clothing_season="winter",
        schedule_id=plan["id"],
        schedule_version=1,
    )
    assert result.status_code == 201, result.text
    assert result.json()["weather"]["source"] == "manual"
    assert result.json()["context"]["season"] is None
    assert "1단계" in result.json()["outfits"][0]["wearing_plan"][0]["note"]
    for role in ("top", "bottom"):
        api_client.patch(
            "/api/v1/garments/" + closet[role], headers=auth_account["headers"], json={"thermal_level": 1}
        )
    light = add_garment(auth_account, "outerwear")
    api_client.patch("/api/v1/garments/" + light, headers=auth_account["headers"], json={"thermal_level": 1})
    result = recommend(
        api_client,
        auth_account,
        temperature_c=28,
        clothing_season="summer",
        schedule_id=plan["id"],
        schedule_version=1,
        outerwear_policy="removable",
    )
    assert result.status_code == 201, result.text
    assert result.json()["outfits"][0]["items"]["outerwear"] == light
    assert result.json()["outfits"][0]["wearing_plan"][0]["outerwear_action"] == "wear"

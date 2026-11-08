from uuid import UUID

import httpx
import pytest
from sqlalchemy import func, select

from backend.app.adapters.recommender import MockRecommender, SafeRecommender
from backend.app.adapters.weather import OpenMeteoWeather
from backend.app.core.config import Settings
from backend.app.db.models import Recommendation, RecommendationRateLimit
from backend.app.deps import get_recommender, get_weather
from backend.tests.adapters.test_weather import response_body


def use_weather(client, response=None):
    calls = []

    def respond(request):
        calls.append(request)
        return response or httpx.Response(200, json=response_body(6.5))

    adapter = OpenMeteoWeather(Settings(_env_file=None), transport=httpx.MockTransport(respond))
    client.app.dependency_overrides[get_weather] = lambda: adapter
    return calls


def test_weather_frontend_contract_requires_auth_and_preserves_rate_allowance(
    api_client,
    auth_account,
    db_session,
):
    calls = use_weather(api_client)
    path = "/api/v1/weather?lat=37.5665&lon=126.978"
    assert api_client.get(path).status_code == 401 and not calls
    response = api_client.get(path, headers=auth_account["headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["temperature_c"] == 6.5 and body["condition"] == "맑음"
    assert body["location_name"] is None and body["source"] == "open_meteo"
    assert not body["is_fallback"] and "Open-Meteo" in body["attribution"]
    assert response.headers["cache-control"] == "private, no-store"
    assert api_client.get(path, headers=auth_account["headers"]).json() == body and len(calls) == 1
    assert db_session.get(RecommendationRateLimit, UUID(auth_account["id"])) is None
    assert api_client.delete("/api/v1/auth/me", headers=auth_account["headers"]).status_code == 204
    assert api_client.get(path, headers=auth_account["headers"]).status_code == 401 and len(calls) == 1


@pytest.mark.parametrize(
    "query",
    [
        "",
        "lat=37",
        "lon=127",
        "lat=91&lon=127",
        "lat=37&lon=-181",
        "lat=nan&lon=127",
        "lat=37&lon=inf",
        "lat=no&lon=127",
        "lat=37&lon=127&temperature_c=100",
    ],
)
def test_invalid_weather_location_is_rejected_before_lookup(api_client, auth_account, query):
    calls = use_weather(api_client)
    response = api_client.get("/api/v1/weather?" + query, headers=auth_account["headers"])
    assert response.status_code == 422 and not calls


def test_weather_failure_requests_manual_input_or_returns_explicit_fallback(api_client, auth_account):
    calls = use_weather(api_client, httpx.Response(503))
    path = "/api/v1/weather?lat=37&lon=127"
    response = api_client.get(path, headers=auth_account["headers"])
    assert response.status_code == 503 and response.json()["code"] == "WEATHER_UNAVAILABLE"
    assert "temperature_c" not in response.json()  # 임의의 0도/평균 기온을 만들지 않음
    manual = api_client.get(path + "&temperature_c=0", headers=auth_account["headers"])
    assert manual.status_code == 200 and manual.json()["temperature_c"] == 0
    assert manual.json()["source"] == "manual" and manual.json()["is_fallback"]
    assert len(calls) == 2


def test_automatic_temperature_reaches_engine_and_postgres_without_coordinates(
    api_client,
    auth_account,
    account_factory,
    closet,
    db_session,
):
    calls = use_weather(api_client)
    seen = []

    class Capture(MockRecommender):
        def recommend(self, context, garments):
            seen.append(context.model_dump())
            return super().recommend(context, garments)

    api_client.app.dependency_overrides[get_recommender] = lambda: SafeRecommender(Capture(), 1)
    response = api_client.post(
        "/api/v1/recommendations",
        headers=auth_account["headers"],
        json={
            "lat": 37.5665,
            "lon": 126.978,
            "temperature_c": 25,
            "user_id": "me",
            "recent_feedback": [],
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["context"]["temperature_c"] == 6.5 and seen[0]["temperature_c"] == 6.5
    assert body["outfits"][0]["items"]["outerwear"] == closet["outerwear"]
    assert body["weather"]["source"] == "open_meteo" and not body["weather"]["is_fallback"]
    stored = db_session.get(Recommendation, UUID(body["recommendation_id"]))
    assert stored.context["weather"] == body["weather"]
    for private in ("lat", "lon", "latitude", "longitude", "37.5665", "126.978"):
        assert private not in str(stored.context)
    db_session.expunge_all()
    path = f"/api/v1/recommendations/{body['recommendation_id']}"
    assert api_client.get(path, headers=auth_account["headers"]).json() == body
    other = account_factory()
    assert api_client.get(path, headers=other["headers"]).status_code == 404
    assert api_client.get("/api/v1/recommendations", headers=other["headers"]).json() == []
    assert len(calls) == 1


@pytest.mark.parametrize("temperature", [0, -5.5, 23])
def test_weather_failure_saves_user_supplied_temperature(
    api_client, auth_account, closet, db_session, temperature
):
    use_weather(api_client, httpx.Response(500))
    response = api_client.post(
        "/api/v1/recommendations",
        headers=auth_account["headers"],
        json={
            "lat": 0,
            "lon": 0,
            "temperature_c": temperature,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["weather"]["is_fallback"] and body["weather"]["source"] == "manual"
    assert body["context"]["temperature_c"] == temperature
    record = db_session.get(Recommendation, UUID(body["recommendation_id"]))
    assert record.context["temperature_c"] == temperature


def test_missing_manual_temperature_on_failure_does_not_save_recommendation(
    api_client, auth_account, closet, db_session
):
    use_weather(api_client, httpx.Response(500))
    response = api_client.post(
        "/api/v1/recommendations", headers=auth_account["headers"], json={"lat": 37, "lon": 127}
    )
    assert response.status_code == 503 and response.json()["code"] == "WEATHER_UNAVAILABLE"
    assert db_session.scalar(select(func.count()).select_from(Recommendation)) == 0
    assert len(db_session.get(RecommendationRateLimit, UUID(auth_account["id"])).request_times) == 1


def test_direct_temperature_skips_lookup_and_legacy_history_remains_readable(
    api_client, auth_account, closet, db_session
):
    calls = use_weather(api_client)
    response = api_client.post(
        "/api/v1/recommendations", headers=auth_account["headers"], json={"temperature_c": 23}
    )
    assert response.status_code == 201 and not calls
    body = response.json()
    assert body["weather"]["source"] == "manual" and not body["weather"]["is_fallback"]
    assert body["outfits"][0]["items"]["outerwear"] is None
    record = db_session.get(Recommendation, UUID(body["recommendation_id"]))
    record.context = {k: v for k, v in record.context.items() if k != "weather"}
    db_session.commit()
    legacy = api_client.get(f"/api/v1/recommendations/{record.id}", headers=auth_account["headers"]).json()
    assert legacy["weather"] is None and legacy["outfits"] == body["outfits"]


@pytest.mark.parametrize(
    "payload",
    [{}, {"lat": 37}, {"lon": 127, "temperature_c": 20}, {"lat": 91, "lon": 127}, {"temperature_c": None}],
)
def test_invalid_recommendation_location_does_not_lookup(api_client, auth_account, payload):
    calls = use_weather(api_client)
    response = api_client.post("/api/v1/recommendations", headers=auth_account["headers"], json=payload)
    assert response.status_code == 422 and not calls

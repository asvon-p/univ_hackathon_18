import json
from datetime import UTC, datetime
from io import BytesIO
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import pytest
from PIL import Image

from backend.tests.e2e.recommendation_flow import submit_recommendation
from backend.tests.e2e.test_thermal_feedback import RegenerateForm
from shared.weather_forecast import KST


@pytest.mark.parametrize("live_api", [{"provider": "local", "weather_stub": True}], indirect=True)
@pytest.mark.parametrize("live_frontend", [{}, {"mentor": True}], indirect=True)
def test_forecast_and_season_survive_navigation_regeneration_and_refresh(live_frontend, tmp_path):
    ui, api, _ = live_frontend
    credentials = {"email": f"forecast-{uuid4().hex}@example.com", "password": "ForecastFixture123!"}
    signup = api.post("/api/v1/auth/signup", json=credentials)
    assert signup.status_code == 201
    headers = {"Authorization": "Bearer " + signup.json()["access_token"]}
    assert ui.post("/login", data=credentials).status_code == 303
    (tmp_path / "weather-scenario.json").write_text(json.dumps({"forecast": True}))
    weather = ui.get("/weather?lat=37.57&lon=126.98").json()
    assert weather["forecast_token"] and weather["forecast"]["minimum_c"] == 3
    image = BytesIO()
    Image.new("RGB", (32, 32), "navy").save(image, format="PNG")
    ids = {}
    for role, level in [("top", 3), ("bottom", 3), ("outerwear", 4)]:
        res = api.post(
            "/api/v1/garments",
            headers=headers,
            files={"file": ("fixture.png", image.getvalue(), "image/png")},
        )
        assert res.status_code == 201
        ids[role] = res.json()["garment_id"]
        assert (
            api.patch(
                "/api/v1/garments/" + ids[role],
                headers=headers,
                json={
                    "category": role,
                    "thermal_level": level,
                    "color": "navy",
                    "style_candidates": ["minimal"],
                },
            ).status_code
            == 200
        )
    date = datetime.now(UTC).astimezone(KST).date().isoformat()
    plan = api.post(
        "/api/v1/day-plans",
        headers=headers,
        json={
            "date": date,
            "events": [
                {
                    "start_at": date + "T15:00:00+09:00",
                    "end_at": date + "T23:00:00+09:00",
                    "purpose": "class",
                    "environment": "indoor",
                }
            ],
        },
    ).json()
    params = {
        "weather_mode": "forecast",
        "temp": weather["temperature_c"],
        "forecast_token": weather["forecast_token"],
        "clothing_season": "winter",
        "schedule_id": plan["id"],
        "schedule_version": plan["version"],
        "required": ids["top"],
        "styles": ["minimal"],
        "wardrobe": "personal",
        "outerwear_policy": "removable",
    }
    form = ui.get("/recommend", params=params)
    assert form.status_code == 200 and "실내 복장에 적용할 계절" in form.text
    back = "/recommend?" + urlencode(params, doseq=True)
    manage = ui.get("/day-plans", params={"back": back})
    assert manage.status_code == 200 and "clothing_season" in manage.text
    from frontend.recommendation_navigation import with_schedule

    carried = parse_qs(urlsplit(with_schedule(back, plan["id"], plan["version"])).query)
    assert carried["forecast_token"] == [weather["forecast_token"]] and carried["clothing_season"] == [
        "winter"
    ]
    result = submit_recommendation(ui, params)
    assert result.status_code == 200, result.text[:700]
    assert "오늘 24시 전 예상" in result.text and "겨울 실내" in result.text
    rid = result.url.path.rsplit("/", 1)[-1]
    saved = api.get("/api/v1/recommendations/" + rid, headers=headers).json()
    assert saved["context"]["clothing_season"] == "winter"
    assert saved["context"]["weather_forecast"]["minimum_c"] == 3
    assert ui.get(result.url.path).status_code == 200
    assert api.get("/api/v1/recommendations/" + rid, headers=headers).json() == saved
    regen = RegenerateForm(result.text)
    response = ui.post(
        "/recommend",
        content=urlencode(regen.values),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert response.status_code == 303, response.text[:700]
    new = api.get(
        "/api/v1/recommendations/" + response.headers["location"].rsplit("/", 1)[-1], headers=headers
    ).json()
    assert new["context"]["weather_forecast"]["start_at"] >= saved["context"]["weather_forecast"]["start_at"]
    assert new["context"]["required_garment_ids"] == [ids["top"]]

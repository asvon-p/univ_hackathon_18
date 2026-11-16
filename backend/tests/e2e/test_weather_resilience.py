import json
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest

from backend.tests.e2e.recommendation_flow import submit_recommendation


@pytest.mark.parametrize(
    "live_api",
    [{"provider": "local", "weather_stub": True, "weather_ttl": 1, "starter_wardrobe": True}],
    indirect=True,
)
def test_concurrent_weather_stale_home_retry_and_recommendation(live_frontend, tmp_path):
    ui, backend, runtime = live_frontend
    password = "WeatherTest123!"
    signup = ui.post(
        "/signup",
        data={
            "email": f"weather-{uuid4().hex}@example.com",
            "password": password,
            "password_confirm": password,
        },
    )
    assert signup.status_code == 303
    token = ui.cookies["fitjang_token"]
    other = {"email": f"weather-other-{uuid4().hex}@example.com", "password": password}
    assert backend.post("/api/v1/auth/signup", json=other).status_code == 201
    other_token = backend.post("/api/v1/auth/login", json=other).json()["access_token"]
    path = tmp_path / "weather-scenario.json"
    path.write_text(json.dumps({"delay": 0.3}))

    def lookup(index):
        with httpx.Client(
            base_url=str(ui.base_url),
            cookies={"fitjang_token": token if index % 2 else other_token},
            trust_env=False,
            timeout=10,
        ) as c:
            return c.get("/weather?lat=37.57&lon=126.98")

    with ThreadPoolExecutor(max_workers=12) as pool:
        replies = list(pool.map(lookup, range(12)))
    assert all(r.status_code == 200 and r.json()["temperature_c"] == 6.5 for r in replies)
    assert len((tmp_path / "weather-calls.jsonl").read_text().splitlines()) == 1
    path.write_text(json.dumps({"fail": True}))
    time.sleep(1.1)
    stale = ui.get("/weather?lat=37.57&lon=126.98").json()
    assert stale["is_stale"] and stale["fetched_at"] and stale["notice"]
    home = ui.get("/")
    assert home.status_code == 200 and 'id="home-weather-retry"' in home.text
    assert "현재와 다를 수 있어요" in home.text and "확인 시각" in home.text
    failed = ui.get("/weather?lat=0&lon=0")
    assert failed.status_code == 503 and failed.headers["retry-after"] == "3"
    path.write_text("{}")
    time.sleep(3.1)
    assert not ui.get("/weather?lat=37.57&lon=126.98").json()["is_stale"]
    result = submit_recommendation(ui, {"temp": "6.5", "occasion": "daily"})
    assert result.status_code == 200

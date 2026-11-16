"""E2E 전용: 외부 날씨 HTTP만 재현. 앱·DB·JWT·추천 엔진은 실제 코드를 사용합니다."""

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx

from backend.app.adapters.weather import OpenMeteoWeather
from backend.app.core.config import get_settings
from backend.app.deps import get_weather
from backend.app.main import create_app


async def respond(request):
    assert request.url.host == "api.open-meteo.com"
    path = Path(os.environ.get("TEST_WEATHER_SCENARIO", "missing-weather-scenario"))
    scenario = json.loads(path.read_text()) if path.is_file() else {}
    if os.environ.get("TEST_WEATHER_CALLS"):
        with Path(os.environ["TEST_WEATHER_CALLS"]).open("a") as stream:
            stream.write("{}\n")
    await asyncio.sleep(scenario.get("delay", 0))
    if scenario.get("fail"):
        return httpx.Response(503)
    if float(request.url.params["longitude"]) == 0:
        return httpx.Response(503)
    return httpx.Response(
        200,
        json={
            "current_units": {"temperature_2m": "°C", "time": "unixtime"},
            "current": {"temperature_2m": 6.5, "weather_code": 0, "time": int(datetime.now(UTC).timestamp())},
        },
    )


app = create_app()
adapter = OpenMeteoWeather(get_settings(), transport=httpx.MockTransport(respond))
app.dependency_overrides[get_weather] = lambda: adapter

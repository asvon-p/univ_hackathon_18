"""E2E 전용: 외부 날씨 HTTP만 재현. 앱·DB·JWT·추천 엔진은 실제 코드를 사용합니다."""

from datetime import UTC, datetime

import httpx

from backend.app.adapters.weather import OpenMeteoWeather
from backend.app.core.config import get_settings
from backend.app.deps import get_weather
from backend.app.main import create_app


def respond(request):
    assert request.url.host == "api.open-meteo.com"
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

import copy
import logging
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from backend.app.adapters.weather import OpenMeteoWeather
from backend.app.core.config import Settings
from backend.app.core.logging import WeatherQueryFilter


def response_body(temperature=8.5, *, code=0, age=0):
    return {
        "current_units": {"temperature_2m": "°C", "time": "unixtime"},
        "current": {
            "temperature_2m": temperature,
            "weather_code": code,
            "time": int((datetime.now(UTC) - timedelta(seconds=age)).timestamp()),
        },
    }


def engine(handler, **options):
    return OpenMeteoWeather(
        Settings(_env_file=None, **options),
        transport=httpx.MockTransport(handler),
    )


def test_valid_weather_uses_celsius_rounds_coordinates_and_caches():
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json=response_body(0))

    weather = engine(respond)
    first = weather.current(37.5665, 126.9780)
    assert first.temperature_c == 0 and first.condition == "맑음"
    assert first.source == "open_meteo" and first.weather_time.tzinfo is not None
    assert weather.current(37.5670, 126.9790) == first
    assert len(seen) == 1
    assert seen[0].url.params["latitude"] == "37.57"
    assert seen[0].url.params["longitude"] == "126.98"
    assert seen[0].url.params["current"] == "temperature_2m,weather_code"
    assert seen[0].url.params["temperature_unit"] == "celsius"
    assert "authorization" not in seen[0].headers and "apikey" not in seen[0].url.params
    assert "lat" not in first.model_dump() and "lon" not in first.model_dump()


@pytest.mark.parametrize(
    "failure",
    [
        "http",
        "timeout",
        "invalid-json",
        "missing",
        "null",
        "string",
        "boolean",
        "out-of-range",
        "fahrenheit",
        "iso-time",
        "old",
        "future",
        "missing-time",
        "missing-unit",
        "redirect",
    ],
)
def test_weather_failure_returns_none_without_caching_or_leaking_location(failure, caplog):
    calls = []

    def respond(request):
        calls.append(request)
        if failure == "http":
            return httpx.Response(429, text="private upstream error")
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://untrusted.invalid"})
        if failure == "timeout":
            raise httpx.ReadTimeout("37.5665,126.978 private upstream error", request=request)
        if failure == "invalid-json":
            return httpx.Response(200, text="private upstream error")
        body = response_body()
        if failure == "missing":
            body.pop("current")
        elif failure in ("null", "string", "boolean", "out-of-range"):
            body["current"]["temperature_2m"] = {
                "null": None,
                "string": "8",
                "boolean": True,
                "out-of-range": 300,
            }[failure]
        elif failure == "fahrenheit":
            body["current_units"]["temperature_2m"] = "°F"
        elif failure == "iso-time":
            body["current_units"]["time"] = "iso8601"
        elif failure == "old":
            body = response_body(age=7201)
        elif failure == "future":
            body = response_body(age=-1800)
        elif failure == "missing-time":
            body["current"].pop("time")
        elif failure == "missing-unit":
            body.pop("current_units")
        return httpx.Response(200, json=body)

    weather = engine(respond)
    assert weather.current(37.5665, 126.9780) is None
    assert weather.current(37.5665, 126.9780) is None
    assert len(calls) == 2
    assert "37.5665" not in caplog.text and "private upstream error" not in caplog.text


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_weather_is_rejected(value):
    # httpx의 JSON 인코더 검증을 거치지 않고 실제 잘못된 공급자 JSON을 재현합니다.
    text = response_body()
    import json

    text["current"]["temperature_2m"] = value
    weather = engine(lambda request: httpx.Response(200, text=json.dumps(text)))
    assert weather.current(37, 127) is None


def test_expired_cache_failure_does_not_return_stale_or_other_location_weather():
    tick = [0.0]
    responses = [response_body(8.5), response_body(24.5), None]
    calls = []

    def respond(request):
        body = responses[len(calls)]
        calls.append(request)
        return httpx.Response(200, json=copy.deepcopy(body)) if body else httpx.Response(503)

    weather = engine(respond, WEATHER_CACHE_TTL_SEC=10, WEATHER_CACHE_MAX_ENTRIES=1)
    weather.clock = lambda: tick[0]
    assert weather.current(37, 127).temperature_c == 8.5
    assert weather.current(35, 129).temperature_c == 24.5
    assert len(weather._cache) == 1
    tick[0] = 11
    assert weather.current(35, 129) is None


def test_outer_timeout_returns_without_waiting_for_upstream():
    def slow(request):
        time.sleep(0.2)
        return httpx.Response(200, json=response_body())

    weather = engine(slow, WEATHER_TIMEOUT_SEC=0.01)
    assert weather.current(37, 127) is None
    assert not weather._cache


def test_uvicorn_weather_query_is_redacted():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %s',
        ("127.0.0.1", "GET", "/api/v1/weather?lat=37.5665&lon=126.978", "1.1", 200),
        None,
    )
    WeatherQueryFilter().filter(record)
    assert "/api/v1/weather" in record.getMessage() and "37.5665" not in record.getMessage()


def test_settings_skip_dotenv_without_attempting_file_read(monkeypatch):
    from pydantic_settings.sources import DotEnvSettingsSource

    def forbidden(*args, **kwargs):
        raise AssertionError("dotenv loader must not be called")

    monkeypatch.setattr(DotEnvSettingsSource, "_read_env_file", forbidden)
    monkeypatch.setenv("FITJANG_SKIP_DOTENV", "1")
    assert Settings().WEATHER_TIMEOUT_SEC == 3

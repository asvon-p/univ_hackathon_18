from backend.app.adapters.base import WeatherPort
from backend.app.core.errors import AppError, ErrorCode
from backend.app.schemas.weather import WeatherResponse


def resolve_weather(
    provider: WeatherPort,
    lat: float | None,
    lon: float | None,
    temperature_c: float | None,
) -> WeatherResponse:
    has_location = lat is not None and lon is not None
    if has_location:
        reading = provider.current(lat, lon)
        if reading is not None:
            return reading
    if temperature_c is not None:
        return WeatherResponse(temperature_c=temperature_c, source="manual", is_fallback=has_location)
    raise AppError(
        503,
        ErrorCode.WEATHER_UNAVAILABLE,
        "현재 기온을 불러오지 못했습니다. 기온을 직접 입력해 추천을 요청해 주세요.",
        headers={"Cache-Control": "private, no-store"},
    )

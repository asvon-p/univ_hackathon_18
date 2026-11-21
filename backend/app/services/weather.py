from backend.app.adapters.base import WeatherPort
from backend.app.core.errors import AppError, ErrorCode
from backend.app.schemas.weather import WeatherResponse
from shared.seasonal_weather import ATTRIBUTION, seasonal_temperature
from shared.weather_forecast import refresh_window


def resolve_weather(
    provider: WeatherPort,
    lat: float | None,
    lon: float | None,
    temperature_c: float | None,
    season: str | None = None,
    month: int | None = None,
) -> WeatherResponse:
    # 명시적 계절 선택은 늦게 도착한 위치/기온보다 우선하며 외부 요청도 하지 않습니다.
    if season is not None:
        month, value = seasonal_temperature(season, month)
        return WeatherResponse(
            temperature_c=value,
            source="seasonal",
            is_fallback=True,
            location_name=f"전국 {month}월 평균",
            attribution=ATTRIBUTION,
        )
    has_location = lat is not None and lon is not None
    if has_location:
        reading = provider.current(lat, lon)
        if reading is not None:
            if reading.forecast:
                try:
                    reading = reading.model_copy(update={"forecast": refresh_window(reading.forecast)})
                except ValueError:
                    reading = reading.model_copy(update={"forecast": None})
            return reading
    if temperature_c is not None:
        return WeatherResponse(temperature_c=temperature_c, source="manual", is_fallback=has_location)
    raise AppError(
        503,
        ErrorCode.WEATHER_UNAVAILABLE,
        "날씨 조회가 지연되고 있어요. 잠시 후 다시 시도하거나 "
        "기온 직접 입력·계절과 월 선택으로 추천받아 주세요.",
        headers={"Cache-Control": "private, no-store", "Retry-After": "3"},
    )

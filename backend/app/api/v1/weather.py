from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from backend.app.adapters.base import WeatherPort
from backend.app.deps import get_current_user_id, get_weather
from backend.app.schemas.weather import WeatherResponse
from backend.app.services.weather import resolve_weather

router = APIRouter(prefix="/api/v1/weather", tags=["weather"])


@router.get("", response_model=WeatherResponse, responses={503: {"description": "수동 기온 입력 필요"}})
def get_current_weather(
    response: Response,
    lat: float = Query(ge=-90, le=90, allow_inf_nan=False),
    lon: float = Query(ge=-180, le=180, allow_inf_nan=False),
    temperature_c: float | None = Query(default=None, ge=-90, le=60, allow_inf_nan=False),
    user_id: UUID = Depends(get_current_user_id),
    provider: WeatherPort = Depends(get_weather),
):
    """위치의 현재 기온. 조회 실패 시 전달한 수동 기온 사용, 없으면 503 WEATHER_UNAVAILABLE."""
    response.headers["Cache-Control"] = "private, no-store"
    return resolve_weather(provider, lat, lon, temperature_c)

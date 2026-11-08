"""Open-Meteo 현재 기온. 외부 오류는 수동 입력으로 전환하며 임의 기온을 만들지 않습니다."""

import logging
import math
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import BaseModel, Field

from backend.app.adapters._timeout import run_with_timeout
from backend.app.core.config import Settings
from backend.app.schemas.weather import Temperature, WeatherResponse

log = logging.getLogger(__name__)
CONDITIONS = {
    0: "맑음",
    1: "대체로 맑음",
    2: "구름 조금",
    3: "흐림",
    45: "안개",
    48: "착빙 안개",
    51: "약한 이슬비",
    53: "이슬비",
    55: "강한 이슬비",
    56: "어는 이슬비",
    57: "어는 이슬비",
    61: "약한 비",
    63: "비",
    65: "강한 비",
    66: "어는 비",
    67: "어는 비",
    71: "약한 눈",
    73: "눈",
    75: "강한 눈",
    77: "싸락눈",
    80: "약한 소나기",
    81: "소나기",
    82: "강한 소나기",
    85: "눈 소나기",
    86: "강한 눈 소나기",
    95: "뇌우",
    96: "우박을 동반한 뇌우",
    97: "강한 뇌우",
    99: "우박을 동반한 강한 뇌우",
}


class CurrentData(BaseModel):
    temperature_2m: Temperature
    time: int = Field(strict=True, ge=0)
    weather_code: int | None = Field(default=None, strict=True)


class ManualWeather:
    name = "manual"

    def current(self, lat: float, lon: float) -> WeatherResponse | None:
        return None


class OpenMeteoWeather:
    name = "open_meteo"

    def __init__(
        self, settings: Settings, *, transport: httpx.BaseTransport | None = None, clock=time.monotonic
    ):
        self.timeout = settings.WEATHER_TIMEOUT_SEC
        self.ttl = settings.WEATHER_CACHE_TTL_SEC
        self.max_entries = settings.WEATHER_CACHE_MAX_ENTRIES
        self.transport = transport
        self.clock = clock
        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple[float, float], tuple[float, WeatherResponse]] = OrderedDict()

    def current(self, lat: float, lon: float) -> WeatherResponse | None:
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        # 약 1 km 단위. 원 좌표는 DB·공급자 요청·캐시에 남기지 않습니다.
        key = (round(lat, 2), round(lon, 2))
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > self.clock():
                self._cache.move_to_end(key)
                return cached[1]
            self._cache.pop(key, None)
        try:
            reading = run_with_timeout(self._fetch, self.timeout, *key)
        except Exception as exc:
            # 예외의 URL/본문에는 좌표가 있을 수 있으므로 종류만 기록합니다.
            log.warning("날씨 조회 실패: %s", type(exc).__name__)
            return None
        if self.ttl:
            with self._lock:
                self._cache[key] = (self.clock() + self.ttl, reading)
                self._cache.move_to_end(key)
                while len(self._cache) > self.max_entries:
                    self._cache.popitem(last=False)
        return reading

    def _fetch(self, lat: float, lon: float) -> WeatherResponse:
        with httpx.Client(timeout=self.timeout, transport=self.transport, follow_redirects=False) as client:
            response = client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,weather_code",
                    "temperature_unit": "celsius",
                    "timeformat": "unixtime",
                    "timezone": "GMT",
                },
            )
        if response.status_code != 200:
            raise ValueError("weather HTTP failure")
        body = response.json()
        units = body.get("current_units", {})
        if units.get("temperature_2m") != "°C" or units.get("time") != "unixtime":
            raise ValueError("weather units mismatch")
        data = CurrentData.model_validate(body["current"])
        now = datetime.now(UTC)
        weather_time = datetime.fromtimestamp(data.time, UTC)
        if not now - timedelta(hours=2) <= weather_time <= now + timedelta(minutes=20):
            raise ValueError("weather timestamp out of range")
        return WeatherResponse(
            temperature_c=data.temperature_2m,
            condition=CONDITIONS.get(data.weather_code),
            source="open_meteo",
            weather_time=weather_time,
            fetched_at=now,
            attribution="Weather data by Open-Meteo.com (CC BY 4.0)",
        )

"""Open-Meteo 현재 기온. 외부 오류는 수동 입력으로 전환하며 임의 기온을 만들지 않습니다."""

import asyncio
import logging
import math
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import BaseModel, Field

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


@dataclass
class _Flight:
    deadline: float
    future: Future | None = None
    expired: threading.Event = field(default_factory=threading.Event)


class OpenMeteoWeather:
    name = "open_meteo"

    def __init__(
        self, settings: Settings, *, transport: httpx.BaseTransport | None = None, clock=time.monotonic
    ):
        self.timeout = settings.WEATHER_TIMEOUT_SEC
        self.ttl = settings.WEATHER_CACHE_TTL_SEC
        self.max_entries = settings.WEATHER_CACHE_MAX_ENTRIES
        self.stale_age = settings.WEATHER_STALE_MAX_AGE_SEC
        self.transport = transport
        self.clock = clock
        self._lock = threading.RLock()
        # No AI pool and no unbounded waiting queue: at most four distinct lookups.
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="weather")
        self._inflight: dict[tuple[float, float], _Flight] = {}
        self._retry: OrderedDict[tuple[float, float], float] = OrderedDict()
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
            flight = self._inflight.get(key)
            if flight is None:
                if self._retry.get(key, 0) > self.clock() or len(self._inflight) >= 4:
                    return self._fallback(key)
                flight = _Flight(time.monotonic() + self.timeout)
                self._inflight[key] = flight
                flight.future = self._pool.submit(self._fetch_bounded, key, flight)
                flight.future.add_done_callback(lambda future: self._finished(key, flight, future))
        try:
            reading = flight.future.result(timeout=max(0, flight.deadline - time.monotonic()))
            if flight.expired.is_set() or time.monotonic() > flight.deadline:
                raise FutureTimeout()
            self._finished(key, flight, flight.future)
            return reading
        except FutureTimeout:
            flight.expired.set()
            flight.future.cancel()  # Cancel work that has not started; running HTTP is bounded below.
        except Exception:
            pass  # One sanitized diagnostic per shared lookup, in _finished.
        with self._lock:
            return self._fallback(key)

    def _fallback(self, key):
        cached = self._cache.get(key)
        if cached:
            reading = cached[1]
            age = (datetime.now(UTC) - reading.fetched_at).total_seconds()
            # Also use the injected monotonic cache clock; tests and clock adjustments stay safe.
            age = max(age, self.clock() - (cached[0] - self.ttl))
            observation_age = (datetime.now(UTC) - reading.weather_time).total_seconds()
            if self.stale_age > 0 and 0 <= age <= self.stale_age and observation_age <= 7200:
                return reading.model_copy(
                    update={
                        "is_fallback": True,
                        "is_stale": True,
                        "age_seconds": int(age),
                        "retry_after_sec": 3,
                        "notice": "실시간 조회가 지연되어 최근 확인한 기온을 표시해요. "
                        "현재 기온과 다를 수 있어요.",
                    }
                )
            self._cache.pop(key, None)
        return None

    def _fetch_bounded(self, key, flight):
        async def run():
            # Cancels network I/O and closes the client even when the overall deadline expires.
            return await asyncio.wait_for(self._fetch(*key), max(0, flight.deadline - time.monotonic()))

        return asyncio.run(run())

    def _finished(self, key, flight, future):
        with self._lock:
            if self._inflight.get(key) is not flight:
                return
            try:
                reading = future.result()
                if flight.expired.is_set() or time.monotonic() > flight.deadline:
                    raise TimeoutError()
                if self.ttl or self.stale_age:
                    self._cache[key] = (self.clock() + self.ttl, reading)
                    self._cache.move_to_end(key)
                    while len(self._cache) > self.max_entries:
                        self._cache.popitem(last=False)
                self._retry.pop(key, None)
            except Exception as exc:
                self._retry[key] = self.clock() + 3
                self._retry.move_to_end(key)
                while len(self._retry) > self.max_entries:
                    self._retry.popitem(last=False)
                log.warning("날씨 조회 실패: %s; retry_after=3s", type(exc).__name__)
            finally:
                if self._inflight.get(key) is flight:
                    del self._inflight[key]

    async def _fetch(self, lat: float, lon: float) -> WeatherResponse:
        async with httpx.AsyncClient(
            timeout=self.timeout, transport=self.transport, follow_redirects=False, trust_env=False
        ) as client:
            response = await client.get(
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

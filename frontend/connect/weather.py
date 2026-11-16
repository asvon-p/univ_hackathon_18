"""Authenticated backend weather lookup, including explicitly labelled recent readings."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from . import USE_MOCK, request

SEOUL = (37.5665, 126.9780)  # 위치를 못 받을 때 기준


@dataclass
class Weather:
    temperature_c: float
    condition: str | None = None
    location_name: str | None = None
    is_stale: bool = False
    fetched_at: str | None = None
    weather_time: str | None = None
    age_seconds: int | None = None
    notice: str | None = None


async def get_weather(token: str, lat: float, lon: float) -> Weather:
    if USE_MOCK:
        return await _mock_weather(lat, lon)
    data = await request("GET", "/weather", token=token, params={"lat": lat, "lon": lon})
    return Weather(
        temperature_c=round(float(data["temperature_c"]), 1),
        condition=data.get("condition"),
        location_name=data.get("location_name"),
        is_stale=data.get("is_stale", False),
        fetched_at=data.get("fetched_at"),
        weather_time=data.get("weather_time"),
        age_seconds=data.get("age_seconds"),
        notice=data.get("notice"),
    )


# ---- Mock: 서울 월평균 기온 (실제 날씨와 무관) ---------------------------------
_SEOUL_MONTHLY = [-2.0, 0.5, 5.5, 12.0, 17.5, 22.0, 25.0, 26.0, 21.5, 14.5, 7.0, 0.5]


async def _mock_weather(lat: float, lon: float) -> Weather:
    await asyncio.sleep(0.3)
    month = (datetime.now(UTC) + timedelta(hours=9)).month  # KST
    near_seoul = abs(lat - SEOUL[0]) < 0.3 and abs(lon - SEOUL[1]) < 0.3
    return Weather(
        temperature_c=_SEOUL_MONTHLY[month - 1],
        condition="Mock",
        location_name="서울" if near_seoul else "현재 위치",
    )

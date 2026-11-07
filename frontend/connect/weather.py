"""연결: 오늘 기온 ↔ C (백엔드가 날씨 API 호출)  (상태: 미완성 · Mock으로 동작)

DECISIONS: 날씨 API 키는 서버 .env(WEATHER_API_KEY)에만 → 날씨는 C의 백엔드가 가져오고, 프론트는 결과만 받음.

GET /weather?lat=37.57&lon=126.98        (Authorization: Bearer)   ← 프론트 제안 (C 확인 필요)
  200 {"temperature_c": 7.5, "condition": "맑음", "location_name": "서울 중구"}   ← condition·location_name은 없어도 됨
  위치를 못 받으면 프론트가 서울 좌표로 요청함
"""

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


async def get_weather(token: str, lat: float, lon: float) -> Weather:
    if USE_MOCK:
        return await _mock_weather(lat, lon)
    data = await request(
        "GET", "/weather", token=token, params={"lat": lat, "lon": lon}
    )
    return Weather(
        temperature_c=round(float(data["temperature_c"]), 1),
        condition=data.get("condition"),
        location_name=data.get("location_name"),
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

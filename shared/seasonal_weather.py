"""Location-free fallback: KMA national monthly normals, 1991–2020.

Source (monthly mean temperature table):
https://www.kma.go.kr/kma/flexer/html/press/2021/03/25/ATC202103250932093_863521e7-fd31-4f26-bfce-04a9e14ffb33.hwp.files/Sections1.html
These are climate averages, never current observations or forecasts.
"""

from datetime import datetime, timedelta, timezone
from typing import Literal

Season = Literal["spring", "summer", "autumn", "winter"]
SEASONS = {"spring": "봄", "summer": "여름", "autumn": "가을", "winter": "겨울"}
SEASON_MONTHS = {"spring": [3, 4, 5], "summer": [6, 7, 8], "autumn": [9, 10, 11], "winter": [12, 1, 2]}
MONTHLY_TEMPERATURES = dict(
    enumerate([-0.6, 1.5, 6.4, 12.3, 17.5, 21.6, 24.8, 25.3, 20.7, 14.6, 7.9, 1.5], 1)
)
ATTRIBUTION = "기상청 1991–2020 전국 월평균 기온 · 실제 현재 기온이 아닌 추천용 대체값"


def current_month() -> int:
    return datetime.now(timezone(timedelta(hours=9))).month


def season_for_month(month: int) -> str:
    return next(season for season, months in SEASON_MONTHS.items() if month in months)


def seasonal_temperature(season: str, month: int | None = None) -> tuple[int, float]:
    if season not in SEASON_MONTHS:
        raise ValueError("계절을 선택해 주세요")
    months = SEASON_MONTHS[season]
    if month is None:
        now = current_month()
        month = now if now in months else months[1]
    if month not in months:
        raise ValueError("선택한 계절에 맞는 월을 선택해 주세요")
    return month, MONTHLY_TEMPERATURES[month]

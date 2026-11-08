"""D의 날씨 응답 계약. 좌표·사용자 식별정보를 응답/추천 기록에 포함하지 않습니다."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Temperature = Annotated[float, Field(ge=-90, le=60, allow_inf_nan=False, strict=True)]


class WeatherResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    temperature_c: Temperature
    condition: str | None = None
    location_name: str | None = None
    source: Literal["open_meteo", "manual"]
    weather_time: datetime | None = None
    fetched_at: datetime | None = None
    is_fallback: bool = False
    attribution: str | None = None
